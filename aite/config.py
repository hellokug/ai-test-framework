"""全局配置收口：默认值与环境变量名（CLAUDE.md 原则 5——显式优于隐式）。

所有模块从这里取默认值，禁止在代码里散落魔法地址/端口/连接串。
密钥不入此文件（一律环境变量引用，target.api_key_env 惯例）。
"""

from __future__ import annotations

import os

DEFAULT_DB_URL = "sqlite+aiosqlite:///./aite.db"
DEFAULT_REPORTS_DIR = "./reports"
DEFAULT_PERFDATA_DIR = "./perf_datasets"   # 性能场景数据集生成产物（模块 J）
WEB_HOST = "127.0.0.1"
WEB_PORT = 8300
ENV_DB_URL = "AITE_DB"


def db_url_from_env() -> str:
    """AITE_DB 环境变量优先，缺省本地 SQLite。"""
    return os.environ.get(ENV_DB_URL, DEFAULT_DB_URL)
