"""启动收敛：running 态 run 在进程重启后统一关账为 error（原则 8 状态机闭合）。"""

import pytest

from aite.service import close_interrupted_runs


class FakeRepo:
    def __init__(self, runs):
        self.runs = runs
        self.updated = []

    async def list_runs(self, limit=50, offset=0):
        return self.runs

    async def update_run(self, run_id, **fields):
        self.updated.append((run_id, fields))


@pytest.mark.asyncio
async def test_running_runs_closed_as_error():
    repo = FakeRepo([
        {"id": "a", "status": "running"},
        {"id": "b", "status": "failed"},
        {"id": "c", "status": "running"},
    ])
    assert await close_interrupted_runs(repo) == 2
    assert {rid for rid, _ in repo.updated} == {"a", "c"}
    assert all(f["status"] == "error" for _, f in repo.updated)
    assert all("restarted" in f["error"] for _, f in repo.updated)


@pytest.mark.asyncio
async def test_no_stale_no_writes():
    repo = FakeRepo([{"id": "a", "status": "passed"}])
    assert await close_interrupted_runs(repo) == 0
    assert repo.updated == []
