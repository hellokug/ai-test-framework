"""web_service 域过滤纯函数单测（/runs 与 /stats 的 domain 过滤共用逻辑）。"""

import pytest

from aite.service.web_service import DOMAINS, filter_runs_by_domain, run_domain


def _run(engine=None, chaos=False, **extra):
    config = dict(extra)
    if engine:
        config["engine"] = engine
    if chaos:
        config["chaos"] = [{"injector": "process"}]
    return {"run_name": "r", "status": "passed", "config": config}


class TestRunDomain:
    @pytest.mark.parametrize("engine,expected", [
        ("pytest", "functional"),
        ("evalscope-perf", "perf"),
        ("locust", "perf"),
        ("evalscope", "accuracy"),
        ("evalscope-rageval", "accuracy"),
    ])
    def test_engine_runs(self, engine, expected):
        assert run_domain(_run(engine=engine)) == expected

    def test_self_built_run_defaults_accuracy(self):
        assert run_domain(_run()) == "accuracy"

    def test_chaos_run_is_reliability(self):
        assert run_domain(_run(chaos=True)) == "reliability"

    def test_tool_engine_with_chaos_stays_tool_domain(self):
        # chaos 包裹不改变工具域归类（reliability 仅自研包裹 run）
        assert run_domain(_run(engine="locust", chaos=True)) == "perf"

    def test_missing_config_is_accuracy(self):
        assert run_domain({"run_name": "r", "status": "passed"}) == "accuracy"


class TestFilterRunsByDomain:
    def test_partition(self):
        runs = [_run(engine="pytest"), _run(engine="locust"),
                _run(), _run(chaos=True)]
        assert len(filter_runs_by_domain(runs, "functional")) == 1
        assert len(filter_runs_by_domain(runs, "perf")) == 1
        assert len(filter_runs_by_domain(runs, "accuracy")) == 1
        assert len(filter_runs_by_domain(runs, "reliability")) == 1
        # 四域划分完备：每个 run 恰好落一个域
        total = sum(len(filter_runs_by_domain(runs, d)) for d in DOMAINS)
        assert total == len(runs)

    def test_unknown_domain_raises(self):
        with pytest.raises(ValueError, match="未知域"):
            filter_runs_by_domain([], "nope")
