"""TaskKit 状态机单测：CRUD 守卫 / run·stop·retry / stale 收敛 / 条件更新竞态。

契约见 docs/domain-backends.md §3（状态机）与 §4（接口）。四表同构，测试以
PerfTaskRow 为载体；FakeExecutor 提供可控执行形态（立即返回 / 等待取消 / 慢取消）。
"""

import asyncio

import pytest

from aite.core.taskkit import (
    DomainTaskKit,
    TaskExecutor,
    TaskNotFoundError,
    TaskStateError,
)
from aite.core.types import (
    TASK_CANCELLED,
    TASK_FAILED,
    TASK_PENDING,
    TASK_RUNNING,
    TASK_STALE,
    TASK_STOPPING,
    TASK_SUCCEEDED,
)
from aite.storage.models import PerfTaskRow
from aite.web.events import EventBus


class FakeExecutor(TaskExecutor):
    """可控执行器：result/error 决定成败，wait_cancel 阻塞至停止，slow_cancel 模拟 kill 卡住。"""

    def __init__(self, result: dict | None = None, error: str | None = None,
                 wait_cancel: bool = False, slow_cancel: bool = False) -> None:
        self.result = result
        self.error = error
        self.wait_cancel = wait_cancel
        self.slow_cancel = slow_cancel

    async def execute(self, ctx) -> dict:
        if self.wait_cancel:
            await ctx.cancel_event.wait()
        if self.error:
            raise RuntimeError(self.error)
        return self.result or {"ok": True}

    async def cancel(self, ctx) -> None:
        if self.slow_cancel:
            await asyncio.sleep(5)
        ctx.cancel_event.set()


def make_kit(executor: FakeExecutor, bus: EventBus | None = None) -> DomainTaskKit:
    return DomainTaskKit("perf", PerfTaskRow, executor, bus or EventBus())


async def drain(bus: EventBus, channel: str) -> list[dict]:
    """订阅至 end 帧（历史回放 + 终态收尾），返回全部帧。"""
    return [event async for event in bus.subscribe(channel)]


# ------------------------------------------------------------------ CRUD 守卫
async def test_create_and_pending_update(repo):
    kit = make_kit(FakeExecutor())
    task = await kit.create(repo, {"target": "x"}, name="t1")
    assert task["status"] == TASK_PENDING and task["spec"] == {"target": "x"}
    updated = await kit.update(repo, task["id"], name="t1b", spec={"target": "y"})
    assert updated["name"] == "t1b" and updated["spec"] == {"target": "y"}


async def test_update_rejected_once_left_pending(repo):
    kit = make_kit(FakeExecutor(wait_cancel=True))
    task = await kit.create(repo, {"a": 1})
    await kit.run(repo, task["id"])
    with pytest.raises(TaskStateError):
        await kit.update(repo, task["id"], name="nope")
    await kit.stop(repo, task["id"])


async def test_delete_guards(repo):
    kit = make_kit(FakeExecutor(wait_cancel=True))
    task = await kit.create(repo, {"a": 1})
    await kit.run(repo, task["id"])
    with pytest.raises(TaskStateError):
        await kit.delete(repo, task["id"])          # 非终态不可删
    await kit.stop(repo, task["id"])
    await kit.delete(repo, task["id"])              # 终态可删
    assert await kit.get(repo, task["id"]) is None


async def test_get_missing_raises(repo):
    kit = make_kit(FakeExecutor())
    with pytest.raises(TaskNotFoundError):
        await kit.delete(repo, "no-such-id")


# ------------------------------------------------------------------ run 成败
async def test_run_success_writes_terminal_and_frames(repo):
    bus = EventBus()
    kit = make_kit(FakeExecutor(result={"pass_rate": 1.0}), bus)
    task = await kit.create(repo, {"suite": "s"})
    await kit.run(repo, task["id"])
    done = await kit.wait(repo, task["id"])
    assert done["status"] == TASK_SUCCEEDED and done["result"] == {"pass_rate": 1.0}
    assert done["started_at"] and done["finished_at"]
    assert kit._handles == {}                       # 句柄清理（运行时句柄不残留）
    frames = await drain(bus, kit.channel(task["id"]))
    assert [f["type"] for f in frames] == ["started", "succeeded", "end"]


async def test_run_failure_records_error(repo):
    bus = EventBus()
    kit = make_kit(FakeExecutor(error="boom"), bus)
    task = await kit.create(repo, {"suite": "s"})
    await kit.run(repo, task["id"])
    done = await kit.wait(repo, task["id"])
    assert done["status"] == TASK_FAILED and "boom" in done["error"]
    frames = await drain(bus, kit.channel(task["id"]))
    assert [f["type"] for f in frames] == ["started", "failed", "end"]


async def test_run_only_once(repo):
    kit = make_kit(FakeExecutor(wait_cancel=True))
    task = await kit.create(repo, {"a": 1})
    await kit.run(repo, task["id"])
    with pytest.raises(TaskStateError):
        await kit.run(repo, task["id"])
    await kit.stop(repo, task["id"])


async def test_executor_receives_repo_and_can_emit_raw(repo):
    """ctx.repo 是调用方传入的同一实例；emit_raw 原样透传引擎自带 type 的帧。"""
    bus = EventBus()
    seen: dict = {}

    class Probe(TaskExecutor):
        async def execute(self, ctx) -> dict:
            seen["repo"] = ctx.repo
            ctx.emit_raw({"type": "case_done", "case": "c1"})
            return {"ok": 1}

    kit = DomainTaskKit("perf", PerfTaskRow, Probe(), bus)
    task = await kit.create(repo, {"a": 1})
    await kit.run(repo, task["id"])
    await kit.wait(repo, task["id"])
    assert seen["repo"] is repo
    frames = await drain(bus, kit.channel(task["id"]))
    assert [f["type"] for f in frames] == ["started", "case_done", "succeeded", "end"]


# --------------------------------------------------------------------- stop
async def test_stop_running_task(repo):
    bus = EventBus()
    kit = make_kit(FakeExecutor(wait_cancel=True), bus)
    task = await kit.create(repo, {"a": 1})
    await kit.run(repo, task["id"])
    stopped = await kit.stop(repo, task["id"])
    assert stopped["status"] == TASK_CANCELLED and stopped["finished_at"]
    assert stopped["error"] is None                 # 正常停止不写 error
    frames = await drain(bus, kit.channel(task["id"]))
    assert [f["type"] for f in frames] == ["started", "progress", "cancelled", "end"]


async def test_stop_via_db_flag_cross_process(repo):
    """跨进程停止桥：另一进程只把状态写成 stopping，执行进程轮询发现后
    触发协作 cancel 并落 cancelled 终态（CLI 独立进程停止的真实路径）。"""
    bus = EventBus()
    kit = make_kit(FakeExecutor(wait_cancel=True), bus)
    kit.stop_poll_interval = 0.05
    task = await kit.create(repo, {"a": 1})
    await kit.run(repo, task["id"])
    await repo.update_domain_task(PerfTaskRow, task["id"],
                                  expect_status=TASK_RUNNING, status=TASK_STOPPING)
    done = await kit.wait(repo, task["id"])
    assert done["status"] == TASK_CANCELLED and kit._handles == {}
    frames = await drain(bus, kit.channel(task["id"]))
    assert [f["type"] for f in frames] == ["started", "cancelled", "end"]


async def test_stop_guards_and_idempotency(repo):
    kit = make_kit(FakeExecutor())
    task = await kit.create(repo, {"a": 1})
    with pytest.raises(TaskStateError):
        await kit.stop(repo, task["id"])            # pending 不可停
    await kit.run(repo, task["id"])
    await kit.wait(repo, task["id"])
    with pytest.raises(TaskStateError):
        await kit.stop(repo, task["id"])            # 终态不可停


async def test_stop_timeout_forces_cancel(repo):
    kit = make_kit(FakeExecutor(wait_cancel=True, slow_cancel=True))
    kit.stop_timeout = 0.05                         # 压短兜底窗口
    task = await kit.create(repo, {"a": 1})
    await kit.run(repo, task["id"])
    stopped = await kit.stop(repo, task["id"])
    assert stopped["status"] == TASK_CANCELLED
    assert "stop timed out" in (stopped["error"] or "")


# --------------------------------------------------------------------- retry
async def test_retry_reruns_same_task(repo):
    kit = make_kit(FakeExecutor(error="boom"))
    first = await kit.create(repo, {"suite": "s"}, name="r1")
    await kit.run(repo, first["id"])
    await kit.wait(repo, first["id"])
    second = await kit.retry(repo, first["id"])
    assert second["id"] == first["id"]              # 同一任务重跑，不再新建
    assert second["status"] == TASK_RUNNING
    assert second["retry_of"] is None and second["retry_count"] == 0
    assert second["spec"] == first["spec"] and second["name"] == "r1"
    await kit.wait(repo, first["id"])
    third = await kit.retry(repo, first["id"])
    assert third["id"] == first["id"]
    await kit.wait(repo, first["id"])
    # 执行历史逐轮累积：trigger 逐轮留档，spec 快照对应每轮配置
    final = await kit.get(repo, first["id"])
    hist = final["history"]
    assert [h["n"] for h in hist] == [1, 2, 3]
    assert [h["trigger"] for h in hist] == ["run", "retry", "retry"]
    assert all(h["status"] == TASK_FAILED for h in hist)
    assert all(h["spec"] == {"suite": "s"} for h in hist)
    assert all(h["started_at"] and h["finished_at"] for h in hist)


async def test_retry_requires_terminal(repo):
    kit = make_kit(FakeExecutor(wait_cancel=True))
    task = await kit.create(repo, {"a": 1})
    await kit.run(repo, task["id"])
    with pytest.raises(TaskStateError):
        await kit.retry(repo, task["id"])
    await kit.stop(repo, task["id"])


# ----------------------------------------------------------------- stale 收敛
async def test_mark_stale_converges_midway_states(repo):
    kit = make_kit(FakeExecutor())
    pending = await kit.create(repo, {"a": 1})
    running = await kit.create(repo, {"a": 2})
    stopping = await kit.create(repo, {"a": 3})
    await repo.update_domain_task(PerfTaskRow, running["id"], status=TASK_RUNNING)
    await repo.update_domain_task(PerfTaskRow, stopping["id"], status=TASK_STOPPING)
    count = await kit.mark_stale(repo)
    assert count == 2
    assert (await kit.get(repo, pending["id"]))["status"] == TASK_PENDING    # 排队保留
    assert (await kit.get(repo, running["id"]))["status"] == TASK_STALE
    assert (await kit.get(repo, stopping["id"]))["status"] == TASK_STALE


# ------------------------------------------------------------- 条件更新（repo 层）
async def test_update_expect_status_guards_race(repo):
    task = await repo.create_domain_task(PerfTaskRow, {"a": 1})
    updated = await repo.update_domain_task(
        PerfTaskRow, task["id"], expect_status=TASK_PENDING, status=TASK_RUNNING)
    assert updated["status"] == TASK_RUNNING
    lost = await repo.update_domain_task(
        PerfTaskRow, task["id"], expect_status=TASK_PENDING, status="x")
    assert lost is None                             # 状态不匹配：不写入，调用方重读裁决
    assert (await repo.get_domain_task(PerfTaskRow, task["id"]))["status"] == TASK_RUNNING


# ------------------------------------------------------------------ list 过滤
async def test_list_filters_by_status_and_retry(repo):
    kit = make_kit(FakeExecutor(error="boom"))
    first = await kit.create(repo, {"a": 1})
    await kit.run(repo, first["id"])
    await kit.wait(repo, first["id"])
    await kit.retry(repo, first["id"])
    await kit.wait(repo, first["id"])
    # 重跑不裂变：列表仍只有一个任务，状态为最新终态
    tasks = await kit.list(repo)
    assert [t["id"] for t in tasks] == [first["id"]]
    assert len(await kit.list(repo, status=TASK_FAILED)) == 1
    # retry_of 过滤保留（读旧链数据）：新语义不再写入 → 空
    assert await kit.list(repo, retry_of=first["id"]) == []
