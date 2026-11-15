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
    """可控执行器：wait_cancel 阻塞至立即停止（graceful），wait_force 阻塞至
    强制停止，hang 模拟不响应停止的执行体（桥升级强杀路径）。"""

    def __init__(self, result: dict | None = None, error: str | None = None,
                 wait_cancel: bool = False, wait_force: bool = False,
                 hang: bool = False) -> None:
        self.result = result
        self.error = error
        self.wait_cancel = wait_cancel
        self.wait_force = wait_force
        self.hang = hang

    async def execute(self, ctx) -> dict:
        if self.wait_cancel:
            await ctx.cancel_event.wait()
        if self.wait_force:
            await ctx.force_event.wait()
        if self.hang:
            await asyncio.sleep(30)
        if self.error:
            raise RuntimeError(self.error)
        return self.result or {"ok": True}


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
    await kit.wait(repo, task["id"])                # 停止异步收口 → 终态后可删
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
    """立即停止：stop 立即返回 stopping（不再阻塞等待），执行器排空返回后
    由 _drive 以 cancelled 收口（历史轮带 result keys）。"""
    bus = EventBus()
    kit = make_kit(FakeExecutor(wait_cancel=True,
                                result={"run_id": "r1", "total_cases": 3}), bus)
    task = await kit.create(repo, {"a": 1})
    await kit.run(repo, task["id"])
    stopped = await kit.stop(repo, task["id"])
    assert stopped["status"] == TASK_STOPPING
    done = await kit.wait(repo, task["id"])
    assert done["status"] == TASK_CANCELLED and done["finished_at"]
    assert done["error"] is None                    # 正常停止不写 error
    last = done["history"][-1]
    assert last["stop_mode"] == "graceful"
    assert last["run_id"] == "r1" and last["total_cases"] == 3   # 产物可追溯
    frames = await drain(bus, kit.channel(task["id"]))
    assert [f["type"] for f in frames] == ["started", "progress", "cancelled", "end"]
    assert frames[1]["mode"] == "graceful"


async def test_exception_during_stopping_cancels_with_error(repo):
    """停止竞态异常收口：stop 已落 stopping 后执行器抛错（如排空中评测基建
    崩了）→ 以 cancelled 收口并带 error 文案；不写 failed（覆盖停止语义），
    也不把 stopping 卡成无终态（suite run error 路径 2026-11-15 新增）。"""
    bus = EventBus()
    kit = make_kit(FakeExecutor(wait_cancel=True, error="drain crashed"), bus)
    task = await kit.create(repo, {"a": 1})
    await kit.run(repo, task["id"])
    await kit.stop(repo, task["id"])
    done = await kit.wait(repo, task["id"])
    assert done["status"] == TASK_CANCELLED
    assert "drain crashed" in (done["error"] or "")
    frames = await drain(bus, kit.channel(task["id"]))
    assert [f["type"] for f in frames] == ["started", "progress", "cancelled", "end"]


async def test_stop_force_sets_force_event(repo):
    """强制停止：force_event 置位、历史轮记 stop_mode=force（子进程型执行器
    据此杀树；未返回请求不计入统计由适配器保证，kit 只管状态机）。"""
    bus = EventBus()
    kit = make_kit(FakeExecutor(wait_force=True), bus)
    task = await kit.create(repo, {"a": 1})
    await kit.run(repo, task["id"])
    stopped = await kit.stop(repo, task["id"], mode="force")
    assert stopped["status"] == TASK_STOPPING
    done = await kit.wait(repo, task["id"])
    assert done["status"] == TASK_CANCELLED
    assert done["history"][-1]["stop_mode"] == "force"
    frames = await drain(bus, kit.channel(task["id"]))
    assert [f["type"] for f in frames] == ["started", "progress", "cancelled", "end"]
    assert frames[1]["mode"] == "force"


async def test_stop_invalid_mode_rejected(repo):
    kit = make_kit(FakeExecutor())
    task = await kit.create(repo, {"a": 1})
    await kit.run(repo, task["id"])
    with pytest.raises(TaskStateError, match="mode"):
        await kit.stop(repo, task["id"], mode="nuke")
    await kit.stop(repo, task["id"])                # 收尾


async def test_stop_stopping_escalates_to_force(repo):
    """stopping 中幂等升级：graceful 后再 force → force_event 补置位 +
    历史轮 stop_mode 改写（执行器尚在排空时的反悔路径）。"""
    kit = make_kit(FakeExecutor(wait_cancel=True, wait_force=True))
    task = await kit.create(repo, {"a": 1})
    await kit.run(repo, task["id"])
    await kit.stop(repo, task["id"])                # graceful
    escalated = await kit.stop(repo, task["id"], mode="force")
    assert escalated["status"] == TASK_STOPPING
    done = await kit.wait(repo, task["id"])
    assert done["status"] == TASK_CANCELLED
    assert done["history"][-1]["stop_mode"] == "force"


async def test_stop_via_db_flag_cross_process(repo):
    """跨进程停止桥：另一进程只把状态写成 stopping（无 stop_mode 记录 →
    按 graceful 处理），执行进程轮询发现后置事件并等执行器排空收口。"""
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


async def test_stop_bridge_escalates_to_force_when_hung(repo, monkeypatch):
    """执行体不响应停止：桥等 stop_timeout 后升级 force_event，再宽限仍卡死
    则 cancel 句柄兜底落 cancelled（stop 必达）。"""
    monkeypatch.setattr("aite.core.taskkit.STOP_FORCE_GRACE_S", 0.05)
    kit = make_kit(FakeExecutor(hang=True))
    kit.stop_timeout = 0.05                         # 压短桥等待窗口
    kit.stop_poll_interval = 0.05
    task = await kit.create(repo, {"a": 1})
    await kit.run(repo, task["id"])
    stopped = await kit.stop(repo, task["id"])
    assert stopped["status"] == TASK_STOPPING
    done = await kit.wait(repo, task["id"])
    assert done["status"] == TASK_CANCELLED


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
