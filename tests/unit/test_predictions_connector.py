"""predictions_file 连接器：离线评测入口——从本地 predictions JSONL 查补丁作为响应。"""

import json

import pytest

from aite.connectors.predictions_file import PredictionsFileConnector
from aite.core.loader import TargetConfig
from aite.core.types import InvokeOptions, TestCase


def write_preds(path, records: list[dict]):
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8")


def make_connector(tmp_path, records, **params):
    pred = tmp_path / "predictions.jsonl"
    write_preds(pred, records)
    config = TargetConfig(type="predictions_file",
                          params={"path": str(pred), **params})
    return PredictionsFileConnector(config)


def swe_case(instance_id: str) -> TestCase:
    return TestCase(id=f"case-{instance_id}", input="issue text",
                    metadata={"instance_id": instance_id})


@pytest.mark.asyncio
async def test_returns_patch_by_instance_id(tmp_path):
    conn = make_connector(tmp_path, [
        {"instance_id": "repo__repo-1", "model_patch": "diff --git a"},
        {"instance_id": "repo__repo-2", "model_patch": ""},
    ])
    resp = await conn.invoke(swe_case("repo__repo-1"), InvokeOptions())
    assert resp.error is None
    assert resp.output == "diff --git a"
    assert resp.raw["predictions_file"]["key"] == "repo__repo-1"


@pytest.mark.asyncio
async def test_missing_prediction_is_empty_output_not_error(tmp_path):
    """缺预测 = 该实例未解（SWE-bench 语义），不应伪装成执行错误。"""
    conn = make_connector(tmp_path, [
        {"instance_id": "repo__repo-1", "model_patch": "diff --git a"},
    ])
    resp = await conn.invoke(swe_case("repo__absent"), InvokeOptions())
    assert resp.error is None
    assert resp.output is None
    assert resp.raw["predictions_file"]["missing"] == "repo__absent"


@pytest.mark.asyncio
async def test_falls_back_to_case_id_without_metadata_key(tmp_path):
    conn = make_connector(tmp_path, [
        {"instance_id": "case-x", "model_patch": "patch-by-case-id"},
    ])
    case = TestCase(id="case-x", input="q")
    resp = await conn.invoke(case, InvokeOptions())
    assert resp.output == "patch-by-case-id"


@pytest.mark.asyncio
async def test_custom_key_and_patch_fields(tmp_path):
    conn = make_connector(tmp_path, [
        {"case_id": "c1", "patch": "custom-field-patch"},
    ], key_field="case_id", patch_field="patch")
    resp = await conn.invoke(
        TestCase(id="c1", input="q", metadata={"case_id": "c1"}),
        InvokeOptions())
    assert resp.output == "custom-field-patch"


@pytest.mark.asyncio
async def test_missing_file_is_explicit_error(tmp_path):
    config = TargetConfig(type="predictions_file",
                          params={"path": str(tmp_path / "nope.jsonl")})
    conn = PredictionsFileConnector(config)
    resp = await conn.invoke(swe_case("x"), InvokeOptions())
    assert resp.error is not None and "not found" in resp.error


@pytest.mark.asyncio
async def test_accepts_json_dict_of_records(tmp_path):
    """mini-swe-agent preds.json 形态：{instance_id: {记录}}。"""
    pred = tmp_path / "preds.json"
    pred.write_text(json.dumps({
        "repo__r1": {"instance_id": "repo__r1", "model_patch": "diff-A"},
        "repo__r2": {"instance_id": "repo__r2", "model_patch": "diff-B"},
    }), encoding="utf-8")
    conn = PredictionsFileConnector(TargetConfig(
        type="predictions_file", params={"path": str(pred)}))
    resp = await conn.invoke(swe_case("repo__r2"), InvokeOptions())
    assert resp.output == "diff-B"


@pytest.mark.asyncio
async def test_accepts_json_dict_of_patch_strings(tmp_path):
    """最简形态：{instance_id: "patch 字符串"}——键即 instance_id。"""
    pred = tmp_path / "preds.json"
    pred.write_text(json.dumps({"repo__r1": "diff-plain"}), encoding="utf-8")
    conn = PredictionsFileConnector(TargetConfig(
        type="predictions_file", params={"path": str(pred)}))
    resp = await conn.invoke(swe_case("repo__r1"), InvokeOptions())
    assert resp.output == "diff-plain"


@pytest.mark.asyncio
async def test_accepts_json_list(tmp_path):
    """swebench 官方 .json 形态：list[record]。"""
    pred = tmp_path / "preds.json"
    pred.write_text(json.dumps([
        {"instance_id": "repo__r1", "model_patch": "diff-list"}]), encoding="utf-8")
    conn = PredictionsFileConnector(TargetConfig(
        type="predictions_file", params={"path": str(pred)}))
    resp = await conn.invoke(swe_case("repo__r1"), InvokeOptions())
    assert resp.output == "diff-list"
