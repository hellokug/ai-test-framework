"""数据集管理 catalog 单测：CRUD / 功能摄取校验 / 引用解析（§12）。"""

import json

import pytest

from aite.datasets.catalog import register_entry, resolve_dataset_ref
from aite.functional.ingest import ingest_functional, validate_cases_file

VALID_CASE = {"id": "c1", "desc": "d",
              "messages": [{"role": "user", "content": "hi"}],
              "expect": {"nonempty": True, "finish_reason": "stop"}}


def _write_cases(tmp_path, lines, name="cases.jsonl"):
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(line, ensure_ascii=False) for line in lines),
                 encoding="utf-8")
    return p


async def test_catalog_upsert_get_list_delete(repo):
    row = await register_entry(repo, dataset_type="accuracy", name="gsm8k-v1",
                               source="hf", path="data/datasets/accuracy/gsm8k-v1.jsonl",
                               version="test@42", stats={"count": 10})
    assert row["type"] == "accuracy" and row["stats"]["count"] == 10
    # 同 (type,name) 再登记 = 更新（幂等，id 不变）
    row2 = await register_entry(repo, dataset_type="accuracy", name="gsm8k-v1",
                                source="local", path="x.jsonl", stats={"count": 11})
    assert row2["id"] == row["id"] and row2["stats"]["count"] == 11
    await register_entry(repo, dataset_type="functional", name="ft-1",
                         source="local", path="f/cases.jsonl")
    assert len(await repo.list_dataset_catalog()) == 2
    assert [r["name"] for r in
            await repo.list_dataset_catalog(type_="functional")] == ["ft-1"]
    found = await repo.find_dataset_catalog("accuracy", "gsm8k-v1")
    assert found and found["id"] == row["id"]
    assert await repo.delete_dataset_catalog(row["id"]) is True
    assert await repo.get_dataset_catalog(row["id"]) is None
    assert await repo.delete_dataset_catalog(row["id"]) is False


async def test_catalog_rejects_unknown_type(repo):
    with pytest.raises(ValueError, match="未知数据集类型"):
        await register_entry(repo, dataset_type="reliability", name="x",
                             source="local", path="p")


async def test_functional_ingest_validates_and_registers(repo, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)     # 文件区 FUNCTIONAL_ROOT 相对 CWD
    src = _write_cases(tmp_path, [VALID_CASE, VALID_CASE | {"id": "c2"}])
    row = await ingest_functional(repo, src, "my-ft")
    assert row["stats"]["count"] == 2
    assert row["stats"]["assertion_keys"]["nonempty"] == 2
    from pathlib import Path

    assert Path(row["path"]).is_file()


async def test_functional_ingest_rejects_bad_lines(repo, tmp_path):
    bad = _write_cases(tmp_path, [{"id": "x", "desc": "d", "messages": []}])  # 缺 expect
    with pytest.raises(ValueError):
        await validate_cases_file(bad)
    with pytest.raises(ValueError):
        await ingest_functional(repo, bad, "nope")


async def test_functional_ingest_duplicate_needs_force(repo, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = _write_cases(tmp_path, [VALID_CASE])
    await ingest_functional(repo, src, "dup")
    with pytest.raises(ValueError, match="已存在"):
        await ingest_functional(repo, src, "dup")
    row = await ingest_functional(repo, src, "dup", force=True)
    assert row["name"] == "dup"


async def test_resolve_dataset_ref_forms(repo, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = _write_cases(tmp_path, [VALID_CASE])
    row = await ingest_functional(repo, src, "refme")
    assert (await resolve_dataset_ref(repo, row["id"]))["name"] == "refme"
    assert (await resolve_dataset_ref(repo, "functional:refme"))["id"] == row["id"]
    assert (await resolve_dataset_ref(repo, "refme"))["id"] == row["id"]
    # 跨类型重名 → 必须用 type:name 形式
    await register_entry(repo, dataset_type="accuracy", name="refme",
                         source="local", path="x")
    with pytest.raises(ValueError, match="type:name"):
        await resolve_dataset_ref(repo, "refme")
    with pytest.raises(ValueError, match="未知数据集引用"):
        await resolve_dataset_ref(repo, "no-such-ds")


def test_cli_import_functional_and_list(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from aite.cli.main import app

    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AITE_DB", f"sqlite+aiosqlite:///{tmp_path/'ds.db'}")
    src = _write_cases(tmp_path, [VALID_CASE])
    r = runner.invoke(app, ["dataset", "import-functional", str(src),
                            "--name", "cli-ft"])
    assert r.exit_code == 0, r.output
    r2 = runner.invoke(app, ["dataset", "list", "--type", "functional"])
    assert r2.exit_code == 0 and "cli-ft" in r2.output
    r3 = runner.invoke(app, ["dataset", "show", "functional:cli-ft"])
    assert r3.exit_code == 0 and '"nonempty"' in r3.output.replace(" ", '"nonempty"') \
        or "nonempty" in r3.output


def test_capability_normalize_and_tag_mapping():
    from aite.datasets.catalog import CAPABILITIES, normalize_capabilities, tags_to_capabilities

    # 归一：去空白/小写/去重保序；未知 key 显式报错
    assert normalize_capabilities(
        ["Math", " math ", "code", "MATH"]) == ["math", "code"]
    assert normalize_capabilities(None) == []
    with pytest.raises(ValueError, match="未知能力维度"):
        normalize_capabilities(["nope"])
    # evalscope 原生 tags → 平台画像：MCQ/Chinese 等格式/语言类 tag 不映射
    assert tags_to_capabilities(
        ["Math", "MCQ", "Chinese", "Reasoning"]) == ["math", "reasoning"]
    assert tags_to_capabilities(["Coding", "Agent"]) == ["code", "agent"]
    assert tags_to_capabilities(None) == []
    assert {"knowledge", "math", "code", "reasoning"} <= set(CAPABILITIES)


async def test_register_entry_capabilities_roundtrip(repo):
    row = await register_entry(repo, dataset_type="accuracy", name="cap-ds",
                               source="upload", path="x.jsonl",
                               capabilities=["Math", "reasoning"])
    assert row["capabilities"] == ["math", "reasoning"]
    # capabilities=None（场景生成等既有调用方）不动已有画像
    keep = await register_entry(repo, dataset_type="accuracy", name="cap-ds",
                                source="local", path="y.jsonl")
    assert keep["capabilities"] == ["math", "reasoning"]
    # 空列表 = 显式清空
    clear = await register_entry(repo, dataset_type="accuracy", name="cap-ds",
                                 source="local", path="y.jsonl",
                                 capabilities=[])
    assert clear["capabilities"] == []


def test_ownership_normalize_and_source_derive():
    from aite.datasets.catalog import normalize_ownership, ownership_from_source

    # 归一：去空白/小写；未知值显式报错
    assert normalize_ownership(" Open ") == "open"
    with pytest.raises(ValueError, match="未知来源"):
        normalize_ownership("nope")
    # 缺省推导：hf 导入=开源；上传/生成/内网直链=自建；注册表按 upstream hf: 识别
    assert ownership_from_source("hf") == "open"
    assert ownership_from_source("remote") == "self"
    assert ownership_from_source("upload") == "self"
    assert ownership_from_source("registry", "hf:ceval/ceval-exam") == "open"


async def test_register_entry_ownership_roundtrip(repo):
    row = await register_entry(repo, dataset_type="accuracy", name="own-ds",
                               source="upload", path="x.jsonl")
    assert row["ownership"] == "self"          # upload 缺省推导=自建
    # 上传的公开数据集副本可显式标开源（私有性与获取渠道正交）
    row2 = await register_entry(repo, dataset_type="accuracy", name="own-ds",
                                source="upload", path="x.jsonl",
                                ownership="open")
    assert row2["ownership"] == "open"
