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
