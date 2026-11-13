"""数据集域 CLI 测试（settings / settings-set / upload / download / pull-url）。

与 test_cli_accuracy_assets 同模式：monkeypatch.chdir(tmp) + AITE_DB 环境，
文件区与设置文件相对 CWD 天然隔离。在线拉取 monkeypatch 掉网络层。
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
    for var in ("AITE_DATASETS_DIR", "HF_HOME", "HF_ENDPOINT",
                "AITE_DATASET_REMOTE_URL"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def _qa_jsonl(path, n=2):
    lines = [f'{{"id": "q{i}", "question": "{i}+{i}=?", "answer": "{2 * i}"}}\n'
             for i in range(1, n + 1)]
    path.write_text("".join(lines), encoding="utf-8")
    return path


def test_settings_show_and_set(env):
    shown = runner.invoke(app, ["dataset", "settings"])
    assert shown.exit_code == 0, shown.output
    assert "datasets_dir" in shown.output and "hf_endpoint" in shown.output
    assert "默认" in shown.output

    saved = runner.invoke(
        app, ["dataset", "settings-set", "hf_endpoint", "https://mirror.example.com"])
    assert saved.exit_code == 0, saved.output
    assert "saved" in saved.output

    again = runner.invoke(app, ["dataset", "settings"])
    assert again.exit_code == 0 and "https://mirror.example.com" in again.output
    assert "设置文件" in again.output

    bad = runner.invoke(app, ["dataset", "settings-set", "nope", "x"])
    assert bad.exit_code == 1 and "未知设置项" in bad.output


def test_upload_list_download_roundtrip(env):
    src = _qa_jsonl(env / "toy.jsonl")
    up = runner.invoke(app, [
        "dataset", "upload", str(src), "--type", "accuracy",
        "--name", "toy-qa", "--notes", "CLI 上传"])
    assert up.exit_code == 0, up.output
    assert "uploaded" in up.output and "toy-qa" in up.output
    # 规范化落位：<根>/datasets/accuracy/toy-qa.jsonl
    assert (env / "data" / "datasets" / "accuracy" / "toy-qa.jsonl").is_file()

    dup = runner.invoke(app, [
        "dataset", "upload", str(src), "--type", "accuracy", "--name", "toy-qa"])
    assert dup.exit_code == 1 and "已存在" in dup.output
    forced = runner.invoke(app, [
        "dataset", "upload", str(src), "--type", "accuracy",
        "--name", "toy-qa", "--force"])
    assert forced.exit_code == 0

    listing = runner.invoke(app, ["dataset", "list", "--type", "accuracy"])
    assert listing.exit_code == 0 and "toy-qa" in listing.output

    out = env / "export" / "toy-copy.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    dl = runner.invoke(app, [
        "dataset", "download", "accuracy:toy-qa", "--out", str(out)])
    assert dl.exit_code == 0, dl.output
    assert out.is_file() and out.read_text(encoding="utf-8") == src.read_text(
        encoding="utf-8")

    missing = runner.invoke(app, ["dataset", "download", "accuracy:nope"])
    assert missing.exit_code == 1


def test_pull_url_uses_setting_and_direct_url(env, monkeypatch):
    from aite.service import dataset_files

    seen = {}

    def fake_get(url, timeout=60):
        seen["url"] = url
        return b'{"question": "3+3=?", "answer": "6"}\n'

    monkeypatch.setattr(dataset_files, "_http_get", fake_get)

    no_url = runner.invoke(app, ["dataset", "pull-url", "x", "--type", "accuracy"])
    assert no_url.exit_code == 1 and "remote_url" in no_url.output

    configured = runner.invoke(
        app, ["dataset", "settings-set", "remote_url", "https://obs.example.com/ds/"])
    assert configured.exit_code == 0, configured.output

    pulled = runner.invoke(app, ["dataset", "pull-url", "obs-qa", "--type", "accuracy"])
    assert pulled.exit_code == 0, pulled.output
    assert seen["url"] == "https://obs.example.com/ds/obs-qa.jsonl"
    assert "obs-qa" in pulled.output

    direct = runner.invoke(app, [
        "dataset", "pull-url", "direct", "--type", "accuracy",
        "--url", "https://cdn.example.com/direct.jsonl"])
    assert direct.exit_code == 0, direct.output
    assert seen["url"] == "https://cdn.example.com/direct.jsonl"
