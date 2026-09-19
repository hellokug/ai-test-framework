"""域任务 CLI 冒烟：挂载后的同形七命令走真实 DB（tmp sqlite）端到端。

四域装配（build_*_task_kit）与 task_cli 工厂组合后的用户可见行为：
create / list / get / rm / run / stop / retry。执行器可控（FlakyExecutor），
不触达真实模型端点。
"""

import pytest
from typer.testing import CliRunner

from aite.cli.task_cli import build_task_cli
from aite.core.taskkit import DomainTaskKit, TaskExecutor
from aite.storage.models import PerfTaskRow
from aite.web.events import EventBus

runner = CliRunner()


class FlakyExecutor(TaskExecutor):
    """spec.fail 为真时抛错（run 失败路径），否则返回 {"ok": True}。"""

    async def execute(self, ctx) -> dict:
        if ctx.spec.get("fail"):
            raise RuntimeError("boom")
        return {"ok": True}


@pytest.fixture
def cli(tmp_path, monkeypatch):
    monkeypatch.setenv("AITE_DB", f"sqlite+aiosqlite:///{tmp_path/'cli.db'}")
    return build_task_cli(DomainTaskKit("perf", PerfTaskRow, FlakyExecutor(),
                                        EventBus()))


def _create(cli, spec: str) -> str:
    result = runner.invoke(cli, ["create", "--spec", spec, "--name", "t1"])
    assert result.exit_code == 0, result.output
    # 输出形如 "created <uuid> status=pending"
    return result.output.split()[1]


def test_create_list_get_roundtrip(cli):
    task_id = _create(cli, '{"a": 1}')
    listed = runner.invoke(cli, ["list"])
    assert listed.exit_code == 0 and "(1 shown)" in listed.output
    detail = runner.invoke(cli, ["get", task_id])
    assert detail.exit_code == 0
    assert "pending" in detail.output and task_id in detail.output \
        and '"a"' in detail.output


def test_run_success_prints_result(cli):
    task_id = _create(cli, '{"ok": 1}')
    result = runner.invoke(cli, ["run", task_id])
    assert result.exit_code == 0, result.output
    assert "succeeded" in result.output and '"ok"' in result.output


def test_run_failure_then_retry_chain(cli):
    task_id = _create(cli, '{"fail": 1}')
    failed = runner.invoke(cli, ["run", task_id])
    assert failed.exit_code == 1                      # CI 友好：非 succeeded 退出 1
    assert "failed" in failed.output and "boom" in failed.output

    retried = runner.invoke(cli, ["retry", task_id])
    assert retried.exit_code == 0, retried.output
    new_id = retried.output.split()[2]                # "retry created <uuid> (...)"
    detail = runner.invoke(cli, ["get", new_id])
    assert "retry_count: 1" in detail.output

    filtered = runner.invoke(cli, ["list", "--retry-of", task_id])
    assert filtered.exit_code == 0 and "(1 shown)" in filtered.output


def test_stop_guard_and_rm_terminal_only(cli):
    task_id = _create(cli, '{"a": 1}')
    stopped = runner.invoke(cli, ["stop", task_id])
    assert stopped.exit_code == 1                     # pending 不可停（TaskStateError）
    removed = runner.invoke(cli, ["rm", "--force", task_id])
    assert removed.exit_code == 1                     # pending 不可删（契约：仅终态）

    runner.invoke(cli, ["run", task_id])              # 跑到终态
    removed = runner.invoke(cli, ["rm", "--force", task_id])
    assert removed.exit_code == 0, removed.output
    missing = runner.invoke(cli, ["get", task_id])
    assert missing.exit_code == 1                     # TaskNotFoundError
