"""Logging helpers shared by CLI / engine / web."""

from __future__ import annotations

import logging
import sys

_CONFIGURED = False


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


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"aite.{name}")
