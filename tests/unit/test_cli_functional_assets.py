"""功能域资产 CLI 双形态测试（fscenario / fscript / ftag，V1-PLAN §10.4/§10.5）。

monkeypatch.chdir(tmp) + AITE_DB 环境（与 test_dataset_catalog 同模式）：
场景根默认落在 CWD 下的 data/functional/scenarios，chdir 后天然隔离。
"""

import json

import pytest
from typer.testing import CliRunner

from aite.cli.main import app

runner = CliRunner()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AITE_DB", f"sqlite+aiosqlite:///{tmp_path / 'cli.db'}")
    monkeypatch.setenv("COLUMNS", "240")   # Rich 表格在 80 列会裁掉单元格内容
    return tmp_path


def _case(case_id: str) -> str:
    return json.dumps({"id": case_id, "desc": f"用例 {case_id}",
                       "messages": [{"role": "user", "content": "hi"}],
                       "expect": {"nonempty": True}}, ensure_ascii=False)


def test_scenario_create_list_and_dimension_guard(env):
    r = runner.invoke(app, ["fscenario", "create", "cli_scn",
                            "--name", "CLI 场景", "--dimension", "API 契约"])
    assert r.exit_code == 0, r.output
    listing = runner.invoke(app, ["fscenario", "list"])
    assert listing.exit_code == 0 and "cli_scn" in listing.output
    assert "API 契约" in listing.output
    bad = runner.invoke(app, ["fscenario", "create", "cli_bad",
                              "--dimension", "不存在的标签"])
    assert bad.exit_code == 1 and "未知维度标签" in bad.output
    dup = runner.invoke(app, ["fscenario", "create", "cli_scn"])
    assert dup.exit_code == 1 and "已存在" in dup.output


def test_tag_add_list_and_duplicate(env):
    r = runner.invoke(app, ["ftag", "add", "安全合规"])
    assert r.exit_code == 0, r.output
    listing = runner.invoke(app, ["ftag", "list"])
    assert "安全合规" in listing.output and "builtin" in listing.output
    dup = runner.invoke(app, ["ftag", "add", "安全合规"])
    assert dup.exit_code == 1 and "已存在" in dup.output


def test_script_import_show_list_delete(env):
    src = env / "rows.jsonl"
    src.write_text("\n".join(_case(i) for i in ("r-1", "r-2")) + "\n",
                   encoding="utf-8")
    runner.invoke(app, ["fscenario", "create", "imp_scn"])
    imp = runner.invoke(app, ["fscript", "import", "imp-script",
                              "--scenario", "imp_scn", "--name", "导入脚本",
                              "--file", str(src), "--case-type", "边界"])
    assert imp.exit_code == 0, imp.output
    assert imp.exit_code == 0, imp.output
    assert "2 行" in imp.output
    both = runner.invoke(app, ["fscript", "import", "x2",
                               "--scenario", "imp_scn", "--name", "n",
                               "--file", str(src), "--dataset", "mini"])
    assert both.exit_code == 1
    show = runner.invoke(app, ["fscript", "show", "imp-script"])
    assert show.exit_code == 0 and "r-1" in show.output
    listing = runner.invoke(app, ["fscript", "list"])
    assert "imp-script" in listing.output
    derived = (env / "data" / "functional" / "scenarios" / "imp_scn"
               / "cases.jsonl").read_text(encoding="utf-8")
    assert "r-2" in derived
    dele = runner.invoke(app, ["fscript", "delete", "imp-script", "--yes"])
    assert dele.exit_code == 0, dele.output
    gone = runner.invoke(app, ["fscript", "show", "imp-script"])
    assert gone.exit_code == 1
