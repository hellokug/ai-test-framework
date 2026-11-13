"""精度域资产 CLI 双形态测试（ascenario / ascript，V1-PLAN §10.4 accuracy）。

monkeypatch.chdir(tmp) + AITE_DB 环境（与 test_cli_functional_assets 同模式）：
场景根默认落在 CWD 下的 data/accuracy/scenarios，chdir 后天然隔离。
"""

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


def _spec_file(path, name="cli-smoke"):
    path.write_text(
        "module: accuracy\n"
        f"run_name: {name}\n"
        "engine: evalscope\n"
        "target: {profile: deepseek}\n"
        "tools: {datasets: [gsm8k], limit_per_subset: 5}\n"
        "report: [json]\n"
        "output_dir: ./reports\n",
        encoding="utf-8")
    return path


def test_scenario_and_script_roundtrip(env):
    made = runner.invoke(app, ["ascenario", "create", "cli_scn",
                               "--name", "CLI 精度场景", "--dimension", "Agent"])
    assert made.exit_code == 0, made.output
    listing = runner.invoke(app, ["ascenario", "list"])
    assert listing.exit_code == 0 and "cli_scn" in listing.output
    assert "Agent" in listing.output

    spec = _spec_file(env / "spec.yaml")
    created = runner.invoke(app, ["ascript", "create", "cli-script",
                                  "--scenario", "cli_scn", "--name", "CLI 脚本",
                                  "--file", str(spec)])
    assert created.exit_code == 0, created.output
    assert "1 数据集" in created.output          # 创建回显：数据集数 + 条数口径
    assert (env / "data" / "accuracy" / "scenarios" / "cli_scn" / "scripts"
            / "cli-script.yaml").is_file()

    scripts = runner.invoke(app, ["ascript", "list"])
    assert scripts.exit_code == 0 and "cli-script" in scripts.output
    show = runner.invoke(app, ["ascript", "show", "cli-script"])
    assert show.exit_code == 0 and "run_name: cli-smoke" in show.output

    deleted = runner.invoke(app, ["ascript", "delete", "cli-script", "--yes"])
    assert deleted.exit_code == 0 and "deleted" in deleted.output


def test_scenario_delete_gated_then_force(env):
    spec = _spec_file(env / "del.yaml")
    runner.invoke(app, ["ascenario", "create", "del_scn", "--name", "待删场景"])
    runner.invoke(app, ["ascript", "create", "del-script", "--scenario", "del_scn",
                        "--name", "脚本", "--file", str(spec)])
    gated = runner.invoke(app, ["ascenario", "delete", "del_scn"])
    assert gated.exit_code == 1 and "仍有" in gated.output
    ok = runner.invoke(app, ["ascenario", "delete", "del_scn", "--force"])
    assert ok.exit_code == 0 and "deleted" in ok.output
    gone = runner.invoke(app, ["ascenario", "create", "del_scn"])
    assert gone.exit_code == 0                      # 目录已删，可重建


def test_create_guards(env):
    runner.invoke(app, ["ascenario", "create", "guard_scn"])
    bad_dim = runner.invoke(app, ["ascenario", "create", "guard_bad",
                                  "--dimension", "不存在的标签"])
    assert bad_dim.exit_code == 1 and "未知维度标签" in bad_dim.output
    dup = runner.invoke(app, ["ascenario", "create", "guard_scn"])
    assert dup.exit_code == 1 and "已存在" in dup.output

    spec = _spec_file(env / "spec.yaml")
    runner.invoke(app, ["ascript", "create", "dup-script", "--scenario",
                        "guard_scn", "--file", str(spec)])
    dup_script = runner.invoke(app, ["ascript", "create", "dup-script",
                                     "--scenario", "guard_scn",
                                     "--file", str(spec)])
    assert dup_script.exit_code == 1 and "全平台唯一" in dup_script.output
    bad_spec = env / "bad.yaml"
    bad_spec.write_text("module: accuracy\nrun_name: no-ds\n", encoding="utf-8")
    no_ds = runner.invoke(app, ["ascript", "create", "no-ds", "--scenario",
                                "guard_scn", "--file", str(bad_spec)])
    assert no_ds.exit_code == 1 and "数据集" in no_ds.output
