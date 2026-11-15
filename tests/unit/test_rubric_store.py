"""rubric 文件仓单测：CRUD / 指纹 / 查重 / 缺失语义。"""

import pytest

from aite.service import rubric_store as rs


def test_create_and_fingerprint(tmp_path):
    p = tmp_path / "rubrics.json"
    item = rs.create_rubric("严格二分", "按对错打分 {question}", path=p)
    assert item["name"] == "严格二分"
    assert item["fingerprint"] == rs.fingerprint("按对错打分 {question}")
    assert len(rs.fingerprint("x")) == 12
    assert len(rs.list_rubrics(p)) == 1


def test_duplicate_name_rejected(tmp_path):
    p = tmp_path / "rubrics.json"
    rs.create_rubric("a", "内容一", path=p)
    with pytest.raises(FileExistsError):
        rs.create_rubric("a", "内容二", path=p)


def test_update_changes_fingerprint(tmp_path):
    p = tmp_path / "rubrics.json"
    item = rs.create_rubric("a", "v1", path=p)
    got = rs.update_rubric(item["id"], content="v2 内容", path=p)
    assert got["fingerprint"] == rs.fingerprint("v2 内容")
    assert got["fingerprint"] != item["fingerprint"]


def test_update_name_conflict(tmp_path):
    p = tmp_path / "rubrics.json"
    i1 = rs.create_rubric("a", "x", path=p)
    i2 = rs.create_rubric("b", "y", path=p)
    with pytest.raises(FileExistsError):
        rs.update_rubric(i2["id"], name="a", path=p)
    got = rs.update_rubric(i1["id"], name="a", path=p)  # 改成自己现名允许（no-op 改名）
    assert got["name"] == "a"


def test_delete_and_missing(tmp_path):
    p = tmp_path / "rubrics.json"
    item = rs.create_rubric("a", "x", path=p)
    rs.delete_rubric(item["id"], path=p)
    assert rs.list_rubrics(p) == []
    with pytest.raises(LookupError):
        rs.get_rubric(item["id"], path=p)
    with pytest.raises(LookupError):
        rs.delete_rubric("nope", path=p)


def test_empty_fields_rejected(tmp_path):
    p = tmp_path / "rubrics.json"
    with pytest.raises(ValueError):
        rs.create_rubric("  ", "x", path=p)
    with pytest.raises(ValueError):
        rs.create_rubric("a", "", path=p)
