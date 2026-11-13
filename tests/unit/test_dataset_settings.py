"""数据集路径/在线源设置层单测（datasets/settings）。

monkeypatch.chdir(tmp) 隔离：settings_path 与数据文件根都相对 CWD（./data
约定），chdir 后天然隔离，不污染仓库目录。
"""

import json
from pathlib import Path

import pytest

from aite import config
from aite.datasets import settings as ds_settings
from aite.datasets import userstore


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for var in ("AITE_DATASETS_DIR", "HF_HOME", "HF_ENDPOINT",
                "AITE_DATASET_REMOTE_URL"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def test_defaults_without_settings_file(env):
    values = ds_settings.load_settings()
    assert values == {"datasets_dir": "", "cache_dir": "",
                      "hf_endpoint": "", "remote_url": ""}
    snap = ds_settings.snapshot()
    assert Path(snap["resolved"]["datasets_dir"]) == Path(config.DEFAULT_DATASETS_DIR)
    assert snap["resolved"]["hf_endpoint"] == config.DEFAULT_HF_ENDPOINT
    assert Path(snap["resolved"]["cache_dir"]) == Path(config.DEFAULT_DATASET_CACHE_DIR)
    assert snap["env_locked"] == {k: False for k in ds_settings.FIELDS}


def test_save_load_roundtrip(env):
    saved = ds_settings.save_settings({
        "datasets_dir": "D:/eval-data", "cache_dir": "D:/eval-cache",
        "hf_endpoint": "https://hf-mirror.example.com",
        "remote_url": "https://obs.example.com/bucket/ds/"})
    assert saved["datasets_dir"] == "D:/eval-data"
    on_disk = json.loads(
        (env / "data" / "dataset_settings.json").read_text(encoding="utf-8"))
    assert on_disk["hf_endpoint"] == "https://hf-mirror.example.com"

    snap = ds_settings.snapshot()
    assert Path(snap["resolved"]["datasets_dir"]) == Path("D:/eval-data")
    assert snap["resolved"]["hf_endpoint"] == "https://hf-mirror.example.com"
    assert snap["resolved"]["remote_url"] == "https://obs.example.com/bucket/ds/"
    # hf_endpoint 留空 = 回到默认镜像
    ds_settings.save_settings({"hf_endpoint": ""})
    assert ds_settings.snapshot()["resolved"]["hf_endpoint"] == \
        config.DEFAULT_HF_ENDPOINT


def test_env_overrides_win_over_settings(env, monkeypatch):
    ds_settings.save_settings({
        "datasets_dir": "D:/from-file", "hf_endpoint": "https://from-file",
        "cache_dir": "D:/cache-from-file",
        "remote_url": "https://remote-from-file"})
    monkeypatch.setenv("AITE_DATASETS_DIR", "D:/from-env")
    monkeypatch.setenv("HF_ENDPOINT", "https://from-env")
    monkeypatch.setenv("HF_HOME", "D:/cache-from-env")
    monkeypatch.setenv("AITE_DATASET_REMOTE_URL", "https://remote-from-env")
    snap = ds_settings.snapshot()
    assert Path(snap["resolved"]["datasets_dir"]) == Path("D:/from-env")
    assert snap["resolved"]["hf_endpoint"] == "https://from-env"
    assert Path(snap["resolved"]["cache_dir"]) == Path("D:/cache-from-env")
    assert snap["resolved"]["remote_url"] == "https://remote-from-env"
    assert snap["env_locked"] == {k: True for k in ds_settings.FIELDS}


def test_validation_errors(env):
    with pytest.raises(ValueError, match="未知设置项"):
        ds_settings.save_settings({"nope": "x"})
    with pytest.raises(ValueError, match="须为字符串"):
        ds_settings.save_settings({"datasets_dir": 123})
    with pytest.raises(ValueError, match="http\\(s\\)"):
        ds_settings.save_settings({"hf_endpoint": "ftp://mirror"})
    with pytest.raises(ValueError, match="http\\(s\\)"):
        ds_settings.save_settings({"remote_url": "obs://bucket"})
    with pytest.raises(ValueError, match="不能为空"):
        ds_settings.save_settings({"datasets_dir": ""})
    # 损坏的设置文件降级为默认（不抛错）
    path = ds_settings.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{broken", encoding="utf-8")
    assert ds_settings.load_settings()["datasets_dir"] == ""


def test_userstore_data_root_follows_setting(env):
    assert userstore.data_root() == Path(config.DEFAULT_DATASETS_DIR)
    ds_settings.save_settings({"datasets_dir": "D:/eval-data"})
    assert userstore.data_root() == Path("D:/eval-data")
    assert userstore.dataset_dir("accuracy") == \
        Path("D:/eval-data/datasets/accuracy")
