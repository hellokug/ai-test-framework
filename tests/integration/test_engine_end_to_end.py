"""End-to-end engine tests with the mock connector (no network)."""

from __future__ import annotations

import json

import pytest

from aite.core.engine import ExecutionEngine
from aite.core.loader import load_suite
from tests.fixtures.mock_components import MockConnector


def suite_config(tmp_path, metrics=None, datasets=None, **extra):
    data = {
        "run_name": "e2e-test",
        "target": {"type": "openai_compatible", "base_url": "http://mock.local/v1",
                   "model": "mock", "params": {"temperature": 0.0}, "stream": False},
        "datasets": datasets if datasets is not None else [
            {"type": "local", "path": str(tmp_path / "qa.jsonl")}],
        "metrics": metrics if metrics is not None else [
            {"exact_match": {"threshold": 0.5}}],
        "report": ["json"],
        "output_dir": str(tmp_path / "reports"),
    }
    data.update(extra)
    return load_suite(data)


@pytest.fixture()
def qa_file(tmp_path):
    p = tmp_path / "qa.jsonl"
    p.write_text(json.dumps({"id": "q1", "question": "2+2?", "answer": "4"}) + "\n" +
                 json.dumps({"id": "q2", "question": "capital of France?", "answer": "Paris"}) + "\n",
                 encoding="utf-8")
    return p


@pytest.mark.asyncio
async def test_two_phase_run_pass(repo, qa_file, tmp_path):
    suite = suite_config(tmp_path, metrics=[
        {"exact_match": {"threshold": 0.5}}, "error_rate",
        {"latency_p95_ms": {"threshold": 10000}}])
    engine = ExecutionEngine(repo)
    report = await engine.run_suite(suite, connector_override=MockConnector())
    assert report.status in ("passed", "failed")
    assert report.total_cases == 2
    assert report.error_cases == 0
    exact = report.summary_for("exact_match")
    assert exact is not None and exact.count == 2
    # mock answers contain the expected answers -> exact_match should pass
    assert exact.value == 1.0
    assert report.status == "passed"
    # artifacts written
    assert report.details["reports"]["json"].endswith("report.json")


@pytest.mark.asyncio
async def test_fail_path_and_exit_code(repo, qa_file, tmp_path):
    suite = suite_config(tmp_path, metrics=[{"exact_match": {"threshold": 1.0}}])
    # answers are mock-echoes -> exact match fails
    answers = {"q1": "totally wrong", "q2": "also wrong"}
    engine = ExecutionEngine(repo)
    report = await engine.run_suite(suite, connector_override=MockConnector(answers))
    assert report.summary_for("exact_match").value == 0.0
    assert report.status == "failed" and report.exit_code == 1


@pytest.mark.asyncio
async def test_error_cases_counted(repo, qa_file, tmp_path):
    suite = suite_config(tmp_path, metrics=["error_rate"])
    engine = ExecutionEngine(repo)
    report = await engine.run_suite(
        suite, connector_override=MockConnector(fail_on={"q1"}))
    assert report.error_cases == 1
    assert report.summary_for("error_rate").value == 0.5


def test_metrics_as_bare_string_rejected(qa_file, tmp_path):
    """`metrics: exact_match`（裸字符串）必须在配置层报错，不能被逐字符迭代。"""
    with pytest.raises(ValueError, match="must be a list"):
        suite_config(tmp_path, metrics="exact_match")


def test_metrics_auto_expands_builtin_recommendations(tmp_path):
    """`metrics: auto` 展开为 builtin 数据集的推荐指标（带阈值与参数）。"""
    suite = suite_config(tmp_path, metrics="auto", datasets=[
        {"type": "builtin", "id": "gsm8k"},
        {"type": "builtin", "id": "mmlu"},
    ])
    names = [m.name for m in suite.metrics]
    assert names == ["exact_match", "choice_accuracy"]   # 并集保序
    gsm = suite.metrics[0]
    assert gsm.threshold == 0.5 and gsm.params.get("extract") == "number"


def test_metrics_auto_accepts_list_form(tmp_path):
    suite = suite_config(tmp_path, metrics=["auto"], datasets=[
        {"type": "builtin", "id": "gsm8k"}])
    assert [m.name for m in suite.metrics] == ["exact_match"]


def test_metrics_auto_without_builtin_errors(qa_file, tmp_path):
    """local 数据集没有推荐指标，auto 必须报清晰错误而非空转。"""
    with pytest.raises(ValueError, match="no recommended metrics"):
        suite_config(tmp_path, metrics="auto")


@pytest.mark.asyncio
async def test_unknown_metric_fails_before_execution(repo, qa_file, tmp_path):
    """指标名写错时 fail-fast：不执行任何用例，run 记为 error。"""
    suite = suite_config(tmp_path, metrics=["no_such_metric"])
    engine = ExecutionEngine(repo)
    report = await engine.run_suite(suite, connector_override=MockConnector())
    assert report.status == "error"
    assert report.total_cases == 0
    results = await repo.list_case_results(report.run_id)
    assert results == []          # 没有任何用例被执行（不烧 API 成本）


@pytest.mark.asyncio
async def test_reliability_variants_materialized(repo, qa_file, tmp_path):
    suite = suite_config(
        tmp_path,
        metrics=[{"repeat_consistency": {"threshold": 0.5}}, "stability"],
        datasets=[{"type": "local", "path": str(qa_file), "repeats": 3}],
    )
    engine = ExecutionEngine(repo)
    report = await engine.run_suite(suite, connector_override=MockConnector())
    # 2 base + 2 repeats per case = 6 invocations, 2 base cases counted
    assert report.total_cases == 2
    assert report.summary_for("repeat_consistency").count == 2
    assert report.summary_for("repeat_consistency").value == 1.0


@pytest.mark.asyncio
async def test_builtin_gsm8k_dataset(repo, tmp_path):
    suite = suite_config(
        tmp_path,
        metrics=[{"exact_match": {"threshold": 0.5, "extract": "number"}}],
        datasets=[{"type": "builtin", "id": "gsm8k", "sample": {"n": 6, "seed": 1}}],
    )
    engine = ExecutionEngine(repo)
    report = await engine.run_suite(suite, connector_override=MockConnector())
    assert report.total_cases == 6
    assert report.summary_for("exact_match").value == 1.0


@pytest.mark.asyncio
async def test_group_by_produces_grouped_summary(repo, qa_file, tmp_path):
    qa2 = tmp_path / "qa2.jsonl"
    qa2.write_text(
        json.dumps({"id": "a1", "question": "x", "answer": "42", "topic": "math"}) + "\n" +
        json.dumps({"id": "a2", "question": "y", "answer": "43", "topic": "science"}) + "\n",
        encoding="utf-8")
    suite = suite_config(
        tmp_path, metrics=[{"exact_match": {}}],
        datasets=[{"type": "local", "path": str(qa2), "group_by": "topic"}])
    engine = ExecutionEngine(repo)
    report = await engine.run_suite(suite, connector_override=MockConnector())
    groups = {s.group: s for s in report.summaries if s.group}
    assert set(groups) == {"math", "science"}


@pytest.mark.asyncio
async def test_judge_runs_through_gateway(repo, qa_file, tmp_path):
    from tests.fixtures.mock_components import MockGateway

    suite = suite_config(tmp_path, metrics=[], judges=[{
        "type": "score", "threshold": 7.0,
        "model": {"type": "openai_compatible", "name": "j1",
                  "base_url": "http://mock.local/v1", "model": "mock-judge"},
        "rubric": "score 1-10",
    }])
    engine = ExecutionEngine(repo)
    # inject mock gateway by patching engine's imported reference
    from aite.core import engine as engine_mod

    original = engine_mod.build_gateway

    def fake_build_gateway(config):
        gw = MockGateway(default='{"score": 9, "reasoning": "great"}')
        gw.register_model(config.judges[0].model)
        return gw

    engine_mod.build_gateway = fake_build_gateway
    try:
        report = await engine.run_suite(suite, connector_override=MockConnector())
    finally:
        engine_mod.build_gateway = original
    summary = report.summary_for("score")
    assert summary is not None
    assert summary.value == 0.9          # 9/10 normalized
    assert summary.passed is True        # 9 >= threshold 7 (raw scale)
    assert report.status == "passed"


@pytest.mark.asyncio
async def test_cost_breaker_stops_scheduling(repo, qa_file, tmp_path):
    suite = suite_config(tmp_path, metrics=["error_rate"], max_cost_usd=-1)
    engine = ExecutionEngine(repo)
    report = await engine.run_suite(suite, connector_override=MockConnector())
    assert report.error_cases == 2       # both blocked by breaker
    assert report.summary_for("error_rate").value == 1.0


@pytest.mark.asyncio
async def test_run_row_persisted(repo, qa_file, tmp_path):
    suite = suite_config(tmp_path, metrics=[{"exact_match": {}}])
    engine = ExecutionEngine(repo)
    report = await engine.run_suite(suite, connector_override=MockConnector())
    run = await repo.get_run(report.run_id)
    assert run["status"] == report.status
    assert run["total_cases"] == 2
    results = await repo.list_case_results(report.run_id)
    assert len(results) == 2
    summaries = await repo.list_metric_summaries(report.run_id)
    assert any(s["metric"] == "exact_match" for s in summaries)
