"""校准执行器单测：MockTransport 假判官跑通 execute_calibration 指标与守卫。"""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from aite.service import judge_calibration_task as jc


def _write_ds(tmp_path, humans):
    p = tmp_path / "cal.jsonl"
    p.write_text("\n".join(
        json.dumps({"question": f"Q{i}", "model_output": f"A{i}",
                    "reference_answer": "G", "human_score": h}, ensure_ascii=False)
        for i, h in enumerate(humans)), encoding="utf-8")
    return p


def _ctx(spec, emitted=None):
    return SimpleNamespace(spec=spec, cancel_event=asyncio.Event(),
                           emit=(lambda e: emitted.append(e))
                           if emitted is not None else (lambda e: None))


def _transport(scores):
    """按调用次序回放判官答复（SCORE 行文本）。"""
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        i = counter["n"]
        counter["n"] += 1
        s = scores[min(i, len(scores) - 1)]
        return httpx.Response(200, json={"choices": [{"message": {"content": f"理由\nSCORE: {s}"}}]})

    return httpx.MockTransport(handler)


def test_execute_calibration_metrics(tmp_path):
    ds = _write_ds(tmp_path, [1, 1, 0, 0])
    emitted = []
    ctx = _ctx({"dataset_path": str(ds), "rubric_text": "按标准 {question} 打分",
                "judge": {"base_url": "http://stub/v1", "model": "stub"}}, emitted)
    result = asyncio.run(jc.execute_calibration(ctx, transport=_transport([1, 1, 1, 0])))
    assert result["engine"] == "judge_calibration"
    assert result["total_cases"] == 4 and result["evaluated_cases"] == 4
    rep = result["calibration_report"]
    # human=[1,1,0,0] judge=[1,1,1,0]：po=0.75、pe=0.5 → κ=0.5；漏放 1 行、误杀 0 行
    m = rep["metrics"]
    assert m["agreement"] == 0.75 and m["kappa"] == 0.5
    assert m["miss_rate_漏放"] == 0.25 and m["kill_rate_误杀"] == 0.0
    assert len(rep["rubric_sha256_12"]) == 12
    assert [d["i"] for d in rep["disagree_rows"]] == [3]
    assert rep["judge"].startswith("stub @ http://stub/v1")  # 完整 chat URL 含 /chat/completions
    assert len(emitted) == 4  # 每行一条进度帧


def test_execute_calibration_limit(tmp_path):
    ds = _write_ds(tmp_path, [1, 0])
    ctx = _ctx({"dataset_path": str(ds), "rubric_text": "r",
                "judge": {"base_url": "http://stub/v1", "model": "stub"}, "limit": 1})
    result = asyncio.run(jc.execute_calibration(ctx, transport=_transport([0])))
    assert result["total_cases"] == 1


def test_requires_rubric_and_judge(tmp_path):
    ds = _write_ds(tmp_path, [1])
    base = {"dataset_path": str(ds)}
    with pytest.raises(ValueError):
        asyncio.run(jc.execute_calibration(
            _ctx({**base, "judge": {"base_url": "http://s/v1", "model": "m"}})))
    with pytest.raises(ValueError):
        asyncio.run(jc.execute_calibration(
            _ctx({**base, "rubric_text": "r", "judge": {}})))


def test_resolve_dataset_by_name(tmp_path, monkeypatch):
    from aite.datasets import userstore

    d = tmp_path / "accuracy"
    d.mkdir()
    (d / "mycal.jsonl").write_text(json.dumps(
        {"question": "q", "model_output": "p", "reference_answer": "g", "human_score": 1},
        ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(userstore, "dataset_dir", lambda t: d)
    assert jc.resolve_dataset_file({"dataset": "mycal"}).name == "mycal.jsonl"
    with pytest.raises(FileNotFoundError):
        jc.resolve_dataset_file({"dataset": "nope"})


def test_plain_api_key_rejected():
    """平台侧判官密钥走 api_key_env：明文 api_key 复用 api_endpoint 的拒绝语义。"""
    with pytest.raises(ValueError):
        jc.resolve_judge({"base_url": "http://s/v1", "model": "m", "api_key": "sk-x"})
