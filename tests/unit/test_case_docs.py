"""用例管理服务/仓储单测（§15，T1）：CRUD / 编号自增 / 绑定校验 / 级联删除 /
执行回填查询（latest_case_run 供版本报告与回填器使用）。"""

import pytest

from aite.service import case_docs as cd

BASE = {"domain": "reliability", "title": "kill worker 后 RTO 达标",
        "priority": "P0", "risk": "高",
        "script": {"kind": "suite_case", "case_name": "kill-worker"}}


async def test_create_auto_case_no_and_update(repo):
    row = await cd.create_case_doc(repo, dict(BASE))
    assert row["case_no"] == "TC-REL-0001" and row["status"] == "draft"
    row2 = await cd.create_case_doc(repo, {**BASE, "title": "第二轮"})
    assert row2["case_no"] == "TC-REL-0002"
    func = await cd.create_case_doc(repo, {"domain": "functional",
                                           "title": "流式契约"})
    assert func["case_no"] == "TC-FUNC-0001"     # 分域自增

    updated = await cd.update_case_doc(repo, row["case_no"],
                                       {"status": "active",
                                        "expected": "RTO ≤ 30s"})
    assert updated["status"] == "active" and updated["expected"] == "RTO ≤ 30s"
    with pytest.raises(ValueError, match="未知用例"):
        await cd.update_case_doc(repo, "TC-NOPE-9999", {"status": "active"})


async def test_duplicate_case_no_and_enums(repo):
    await cd.create_case_doc(repo, dict(BASE))
    with pytest.raises(ValueError, match="已存在"):
        await cd.create_case_doc(repo, {**BASE, "case_no": "TC-REL-0001"})
    with pytest.raises(ValueError, match="priority"):
        await cd.create_case_doc(repo, {**BASE, "priority": "P9"})
    with pytest.raises(ValueError, match="domain"):
        await cd.create_case_doc(repo, {"domain": "chaos", "title": "t"})


async def test_script_binding_rules(repo):
    with pytest.raises(ValueError, match="仅用于 reliability"):
        await cd.create_case_doc(repo, {"domain": "perf", "title": "t",
                                        "script": {"kind": "suite_case",
                                                   "case_name": "x"}})
    with pytest.raises(ValueError, match="case_name"):
        await cd.create_case_doc(repo, {**BASE,
                                        "script": {"kind": "suite_case"}})
    with pytest.raises(ValueError, match="script.kind"):
        await cd.create_case_doc(repo, {**BASE,
                                        "script": {"kind": "magic"}})
    # 合法绑定照常
    row = await cd.create_case_doc(repo, {"domain": "perf", "title": "perf 用例",
                                          "script": {"kind": "suite_metric",
                                                     "metric": "ttft_p95_ms",
                                                     "threshold": 2000}})
    assert row["script"]["metric"] == "ttft_p95_ms"


async def test_update_case_doc_binding(repo):
    row = await cd.create_case_doc(repo, dict(BASE))
    updated = await cd.update_case_doc(
        repo, row["case_no"],
        {"script": {"kind": "suite_case", "case_name": "kill-router"}})
    assert updated["script"]["case_name"] == "kill-router"
    with pytest.raises(ValueError, match="不可改"):
        await cd.update_case_doc(repo, row["case_no"], {"domain": "perf"})


async def test_delete_cascades_runs(repo):
    row = await cd.create_case_doc(repo, dict(BASE))
    await repo.create_case_run({"case_id": row["id"], "version": "v1",
                                "task_id": "t1", "verdict": "passed",
                                "key_metrics": {"rto_seconds": 3.2}})
    await repo.create_case_run({"case_id": row["id"], "version": "v2",
                                "task_id": "t2", "verdict": "failed",
                                "key_metrics": {"rto_seconds": 99.0}})
    latest = await repo.latest_case_run(row["id"], version="v2")
    assert latest["verdict"] == "failed"
    runs = await repo.list_case_runs(verdict="passed")
    assert len(runs) == 1
    await repo.delete_case_doc(row["id"])
    assert await repo.list_case_runs(case_id=row["id"]) == []   # 级联删除
