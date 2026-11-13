"""全局配置收口：默认值与环境变量名（CLAUDE.md 原则 5——显式优于隐式）。

所有模块从这里取默认值，禁止在代码里散落魔法地址/端口/连接串。
密钥不入此文件（一律环境变量引用，target.api_key_env 惯例）。
"""

from __future__ import annotations

import os

DEFAULT_DB_URL = "sqlite+aiosqlite:///./aite.db"
DEFAULT_REPORTS_DIR = "./reports"
DEFAULT_PERFDATA_DIR = "./data/datasets/perf"   # 性能场景数据集生成产物（模块 J，按类型归目录）
EVAL_MODULES = ("accuracy", "functional", "perf", "reliability")   # 评测四域（suite.module / 目录归位）
DEFAULT_SWEBENCH_DATASET = "SWE-bench/SWE-bench_Lite"   # swebench v5 默认（原 princeton-nlp）
DEFAULT_SWE_RESOLVE_TIMEOUT_S = 3600.0     # 单实例打分子进程墙钟上限（含镜像拉取）
DEFAULT_SWEBENCH_WORK_DIR = "./data/runs/swebench"
DEFAULT_EVALSCOPE_TASK_TIMEOUT_S = 14400.0  # evalscope agent 任务子进程墙钟上限（ swe-bench 单实例 30min 量级）
DEFAULT_HF_ENDPOINT = "https://hf-mirror.com"   # 子进程 HF 默认镜像（用户显式设置 HF_ENDPOINT 时优先）   # 打分文件契约产物（按模块归目录）
DEFAULT_COMMON_FILE = "./data/common.yaml"   # 全局 common 配置：模型预制（target.profile 引用）+ 工具环境缺省
DEFAULT_FUNCTIONAL_SCENARIOS_DIR = "./data/functional/scenarios"   # 评估场景托管根（引擎缺省同源，V1-PLAN §10.5）
DEFAULT_ACCURACY_SCENARIOS_DIR = "./data/accuracy/scenarios"   # 精度场景托管根（场景/脚本全部用户自管，无预置，V1-PLAN §10.4）
DEFAULT_DATASETS_DIR = "./data"              # 数据集文件根（设置文件 datasets_dir 缺省；目录树 <根>/datasets/<type>/）
DEFAULT_DATASET_CACHE_DIR = "./data/cache/hf"   # 在线数据集下载缓存根（设置文件 cache_dir 缺省，注入 HF_HOME）
DATASET_SETTINGS_FILE = "./data/dataset_settings.json"   # 数据集路径/在线源设置（datasets_dir/cache_dir/hf_endpoint/remote_url）
ENV_DATASETS_DIR = "AITE_DATASETS_DIR"       # 数据集文件根的环境变量覆盖（优先于设置文件）
ENV_DATASET_REMOTE_URL = "AITE_DATASET_REMOTE_URL"   # 远程拉取地址（OBS 桶等）的环境变量覆盖
SECRET_KEY_FILE = "./data/.secret.key"      # 密钥库本地密钥（与库分离，不入 git）
SECRET_STORE_FILE = "./data/.secrets.json"  # 密钥库（api_key_env 未设置时的加密兜底，不入 git）
WEB_HOST = "127.0.0.1"
WEB_PORT = 8300
MAX_DOMAIN_SCAN = 2000   # /runs?domain= 域过滤的最大扫描条数（新→旧，内存过滤后分页）
ENV_DB_URL = "AITE_DB"


def db_url_from_env() -> str:
    """AITE_DB 环境变量优先，缺省本地 SQLite。"""
    return os.environ.get(ENV_DB_URL, DEFAULT_DB_URL)
