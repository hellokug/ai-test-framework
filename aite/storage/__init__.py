"""Repository 会话管理：组合根（CLI/Web）共用的生命周期样板收口。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from aite import config
from aite.storage.db import Database
from aite.storage.repositories import SqlAlchemyRepository

__all__ = ["Database", "SqlAlchemyRepository", "build_repository", "repo_session"]


def build_repository(storage_url: str = config.DEFAULT_DB_URL) -> SqlAlchemyRepository:
    """具体装配收口在 storage 层（组合根调用）；上层只依赖 Repository 抽象。"""
    return SqlAlchemyRepository(Database(storage_url))


@asynccontextmanager
async def repo_session(storage_url: str = config.DEFAULT_DB_URL
                       ) -> AsyncIterator[SqlAlchemyRepository]:
    """init → yield → close 的生命周期样板（消除 CLI 各命令的 try/finally 重复）。"""
    repo = build_repository(storage_url)
    await repo.init()
    try:
        yield repo
    finally:
        await repo.close()
