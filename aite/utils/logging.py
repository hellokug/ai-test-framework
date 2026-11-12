"""Logging helpers shared by CLI / engine / web."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_CONFIGURED = False

APP_LOG_NAME = "aite.log"        # 业务日志（aite.*：422 详情、引擎/服务告警）
SERVER_LOG_NAME = "uvicorn.log"  # Web 服务器日志（HTTP 访问行 + 启停/错误）


def setup_logging(level: str = "INFO", quiet: bool = False) -> None:
    """Configure root logging once; quiet mode keeps stdout clean for Rich/CLI."""
    global _CONFIGURED
    if _CONFIGURED:
        logging.getLogger("aite").setLevel(level.upper())
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.WARNING if quiet else logging.INFO)
    logging.getLogger("aite").setLevel(level.upper())
    for noisy in ("httpx", "httpcore", "aiosqlite", "sqlalchemy", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _CONFIGURED = True


def build_web_log_config(log_dir: Path | str = "logs") -> dict:
    """uvicorn log_config（aite serve 专用）：控制台输出不变之外，日志按来源
    分两个文件落盘（跟随启动时 CWD，与 ./aite.db、./reports 同一约定）——
    ``aite.log`` 只收 aite.* 业务日志；``uvicorn.log`` 收 HTTP 访问行与
    服务器启停/错误（各 5MB × 3 轮转，惰性打开）。uvicorn 是承载 FastAPI
    的 ASGI 服务器，其日志与业务日志同属 Python logging，但关心主体不同，
    故分文件。不复用 uvicorn 的 LOGGING_CONFIG 常量以免 utils 层依赖
    uvicorn；字段与其逐项对齐，仅多挂 file handler。
    """
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)   # RotatingFileHandler 不建父目录
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {"()": "uvicorn.logging.DefaultFormatter",
                        "fmt": "%(levelprefix)s %(message)s", "use_colors": None},
            "access": {"()": "uvicorn.logging.AccessFormatter",
                       "fmt": '%(client_addr)s - "%(request_line)s" %(status_code)s'},
            "file": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"},
        },
        "handlers": {
            "default": {"formatter": "default", "class": "logging.StreamHandler",
                        "stream": "ext://sys.stderr"},
            "access": {"formatter": "access", "class": "logging.StreamHandler",
                       "stream": "ext://sys.stdout"},
            "file_app": {"formatter": "file",
                         "class": "logging.handlers.RotatingFileHandler",
                         "filename": str(log_dir / APP_LOG_NAME),
                         "maxBytes": 5 * 1024 * 1024, "backupCount": 3,
                         "encoding": "utf-8", "delay": True},
            "file_server": {"formatter": "file",
                            "class": "logging.handlers.RotatingFileHandler",
                            "filename": str(log_dir / SERVER_LOG_NAME),
                            "maxBytes": 5 * 1024 * 1024, "backupCount": 3,
                            "encoding": "utf-8", "delay": True},
        },
        "loggers": {
            "uvicorn": {"handlers": ["default", "file_server"], "level": "INFO",
                        "propagate": False},
            "uvicorn.error": {"level": "INFO"},
            "uvicorn.access": {"handlers": ["access", "file_server"], "level": "INFO",
                               "propagate": False},
            "aite": {"handlers": ["default", "file_app"], "level": "INFO",
                     "propagate": False},
        },
    }


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"aite.{name}")
