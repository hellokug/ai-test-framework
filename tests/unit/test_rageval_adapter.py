"""evalscope-rageval 适配器测试（全离线，不依赖 evalscope/ragas 安装）。"""

import json

import pytest

from aite.tools.base import ToolRunRequest
from aite.tools.rageval_adapter import RagevalAdapter


def make_req(tmp_path, datasets, rag_eval=None, target=None):
    return ToolRunRequest(
        run_name="t", target=target or {}, datasets=datasets,
        work_dir=tmp_path, rag_eval=rag_eval or {})


def write_cases(tmp_path, records, name="cases.jsonl"):
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
                 encoding="utf-8")
    return str(p)


def b_cases():
    return [
        {"id": "c1", "question": "长江多长？", "answer": "约6300公里",
         "retrieval_context": ["长江约6300公里。"], "response": "约6300公里。",
         "subset": "read"},
        {"id": "c2", "question": "光速多少？", "answer": "约30万公里/秒",
         "retrieval_context": ["光速约30万公里/秒。"], "response": "30万公里/秒。",
         "subset": "read"},
    ]


class TestBuildConfig:
    def test_happy_shape_and_api_key_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-123")
        req = make_req(tmp_path, ["x.jsonl"], rag_eval={
            "metrics": ["faithfulness", "answer_correctness"],
            "judge": {"base_url": "https://api.deepseek.com/v1",
                      "model": "deepseek-flash", "api_key_env": "DEEPSEEK_API_KEY"},
            "embeddings": {"model_name_or_path": "BAAI/bge-large-zh-v1.5"},
        })
        cfg = RagevalAdapter().build_config(req)
        assert cfg["eval_backend"] == "RAGEval"
        ev = cfg["eval_config"]
        assert ev["tool"] == "ragas"
        assert ev["eval"]["critic_llm"]["model_name"] == "deepseek-flash"
        assert ev["eval"]["critic_llm"]["api_key"] == "sk-test-123"
        assert ev["eval"]["testset_file"] == str(tmp_path / "ragas_testset.json")
        assert ev["eval"]["language"] == "chinese"
        assert ev["eval"]["embeddings"]["model_name_or_path"] == "BAAI/bge-large-zh-v1.5"

    def test_judge_model_required(self, tmp_path):
        req = make_req(tmp_path, ["x.jsonl"], rag_eval={"judge": {}})
        with pytest.raises(ValueError, match="judge.model 必配"):
            RagevalAdapter().build_config(req)

    def test_unknown_metric_rejected(self, tmp_path):
        req = make_req(tmp_path, ["x.jsonl"],
                       rag_eval={"judge": {"model": "j"}, "metrics": ["made_up"]})
        with pytest.raises(ValueError, match="未知指标"):
            RagevalAdapter().build_config(req)

    def test_embeddings_required_unconditionally(self, tmp_path):
        """evalscope RAGASEvalConfig 的 embeddings 为必填字段（2026-09-24 实测）——
        即使只跑纯 LLM 指标也要配（本地小模型即可）。"""
        req = make_req(tmp_path, ["x.jsonl"],
                       rag_eval={"judge": {"model": "j"},
                                 "metrics": ["faithfulness"]})
        with pytest.raises(ValueError, match="embeddings.*必配"):
            RagevalAdapter().build_config(req)


class TestModesAndRun:
    @pytest.fixture
    def no_evalscope(self, monkeypatch):
        """显式模拟 evalscope 不可导入（MetaPathFinder 拦截）——契约测试与宿主
        环境是否安装 evalscope 解耦（conda/.venv 宿主可能真装了）。"""
        import sys

        class _Blocker:
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "evalscope" or fullname.startswith("evalscope."):
                    raise ImportError("blocked by test")
                return None

        monkeypatch.setattr(sys, "meta_path", [_Blocker(), *sys.meta_path])
        for name in [m for m in list(sys.modules)
                     if m == "evalscope" or m.startswith("evalscope.")]:
            monkeypatch.delitem(sys.modules, name, raising=False)

    @pytest.mark.asyncio
    async def test_b_mode_writes_testset_then_requires_evalscope(
            self, tmp_path, no_evalscope):
        """方案 B：records 自带 response → 转 testset 落盘；evalscope 缺失显式报错。"""
        ds = write_cases(tmp_path, b_cases())
        req = make_req(tmp_path, [ds], rag_eval={
            "judge": {"model": "deepseek-flash"},
            "embeddings": {"model_name_or_path": "BAAI/bge-large-zh-v1.5"}})
        with pytest.raises(RuntimeError, match="evalscope\\[rag\\]"):
            await RagevalAdapter().run(req)
        records = json.loads((tmp_path / "ragas_testset.json").read_text(encoding="utf-8"))
        assert records[0]["user_input"] == "长江多长？"
        assert records[0]["response"] == "约6300公里。"
        assert records[0]["retrieved_contexts"] == ["长江约6300公里。"]
        assert records[0]["reference"] == "约6300公里"

    @pytest.mark.asyncio
    async def test_mixed_modes_rejected(self, tmp_path):
        records = b_cases()
        records[1].pop("response")  # 一半有一半没有 → 显式报错
        ds = write_cases(tmp_path, records)
        req = make_req(tmp_path, [ds], rag_eval={"judge": {"model": "j"}})
        with pytest.raises(ValueError, match="response 字段部分缺失"):
            await RagevalAdapter().run(req)

    @pytest.mark.asyncio
    async def test_a_mode_requires_target(self, tmp_path):
        records = [{"id": "c1", "question": "q", "answer": "a",
                    "retrieval_context": ["d"]}]
        ds = write_cases(tmp_path, records)
        req = make_req(tmp_path, [ds], rag_eval={"judge": {"model": "j"}})
        with pytest.raises(ValueError, match="方案 A"):
            await RagevalAdapter().run(req)


class TestCollect:
    def test_score_parsing_means_counts_and_failures(self, tmp_path):
        score = [
            {"user_input": "q1", "faithfulness": 1.0, "context_precision": 0.5},
            {"user_input": "q2", "faithfulness": 0.0, "context_precision": None},
            {"user_input": "q3", "faithfulness": float("nan")},
        ]
        (tmp_path / "ragas_testset_score.json").write_text(
            json.dumps(score), encoding="utf-8")
        req = make_req(tmp_path, ["x.jsonl"], rag_eval={
            "metrics": ["faithfulness", "context_precision"],
            "judge": {"model": "j"}})
        req.metric_thresholds = {"faithfulness": 0.6}
        result = RagevalAdapter().collect(req)
        rows = {r.metric: r for r in result.rows}
        assert rows["faithfulness"].value == pytest.approx(0.5)
        assert rows["faithfulness"].count == 2
        assert rows["faithfulness"].passed is False      # 0.5 < 0.6
        assert rows["context_precision"].value == pytest.approx(0.5)
        assert rows["context_precision"].count == 1
        # 无分判定进失败明细，且 case 下标逐一对应（回归：曾全记最后一个下标）；
        # 缺键/None 同为「无判定」
        assert {f["case"] for f in result.failures} == {
            "context_precision:1", "context_precision:2", "faithfulness:2"}
        assert result.report_files["score"].endswith("ragas_testset_score.json")

    def test_missing_score_file_is_explicit_failure(self, tmp_path):
        req = make_req(tmp_path, ["x.jsonl"], rag_eval={
            "metrics": ["faithfulness"], "judge": {"model": "j"}})
        result = RagevalAdapter().collect(req)
        assert result.rows == []
        assert "score file missing" in result.failures[0]["detail"]


class TestListTasks:
    def test_lists_ragas_five(self):
        names = {t["name"] for t in RagevalAdapter().list_tasks()}
        assert names == {"faithfulness", "answer_relevancy", "context_precision",
                         "context_recall", "answer_correctness"}


def test_parse_tool_suite_rageval_section():
    """suite yaml rageval 段 → ToolRunRequest.rag_eval（datasets 走 tools.datasets）。"""
    from aite.tools import parse_tool_suite

    raw = {
        "engine": "evalscope-rageval",
        "run_name": "ragas-rgb",
        "tools": {"datasets": ["./data/datasets/debug/rgb_openbook.jsonl"],
                  "metric_thresholds": {"faithfulness": 0.6}},
        "rageval": {"metrics": ["faithfulness"],
                    "judge": {"model": "deepseek-flash"}},
    }
    req, _ = parse_tool_suite(raw)
    assert req.datasets == ["./data/datasets/debug/rgb_openbook.jsonl"]
    assert req.rag_eval["judge"]["model"] == "deepseek-flash"
    assert req.metric_thresholds == {"faithfulness": 0.6}


def test_instructor_model_args_patch_injects_max_tokens(monkeypatch):
    """ragas instructor 适配器硬编码 model_args=InstructorModelArgs()（缺省
    max_tokens=1024）——llm_factory kwargs 与 evalscope 配置均不生效 → 子类化
    注入配置（实测 IncompleteOutputException 根因）。"""
    import sys
    import types

    from pydantic import BaseModel

    class InstructorModelArgs(BaseModel):
        temperature: float = 0.01
        max_tokens: int = 1024

    mod = types.ModuleType("ragas.llms.adapters.instructor")
    mod.InstructorModelArgs = InstructorModelArgs
    for name in ("ragas", "ragas.llms", "ragas.llms.adapters", mod.__name__):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, mod.__name__, mod)

    from aite.tools.rageval_adapter import _patch_ragas_instructor_model_args

    _patch_ragas_instructor_model_args({"max_tokens": 4096, "temperature": 0.05})
    args = mod.InstructorModelArgs()
    assert args.max_tokens == 4096 and args.temperature == 0.05
    # 显式传值不被覆盖
    assert mod.InstructorModelArgs(max_tokens=8192).max_tokens == 8192


def test_local_judge_gets_placeholder_key(monkeypatch, tmp_path):
    """Ollama/vLLM 等本地端点无需 key——OpenAI 客户端仍要求非空 → 注入占位；
    api_key_env 指向未设置的环境变量则显式报错（不静默）。"""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    req = make_req(tmp_path, ["x.jsonl"], rag_eval={
        "judge": {"base_url": "http://localhost:11434/v1", "model": "qwen2.5:3b"},
        "embeddings": {"model_name_or_path": "BAAI/bge"},
        "metrics": ["faithfulness"]})
    cfg = RagevalAdapter().build_config(req)
    assert cfg["eval_config"]["eval"]["critic_llm"]["api_key"] == "EMPTY"

    req2 = make_req(tmp_path, ["x.jsonl"], rag_eval={
        "judge": {"base_url": "https://api.deepseek.com/v1", "model": "deepseek-flash",
                  "api_key_env": "DEEPSEEK_API_KEY"},
        "embeddings": {"model_name_or_path": "BAAI/bge"},
        "metrics": ["faithfulness"]})
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY 未设置"):
        RagevalAdapter().build_config(req2)
