"""Async database engine/session factory.

SQLite: WAL journal + busy_timeout enabled on connect; a single-writer session
factory is used. Switching dialects requires only a different ``storage.url``
(``mysql+aiomysql://`` / ``postgresql+asyncpg://``).
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from aite.storage.models import Base

logger = logging.getLogger("aite.storage")


def _sqlite_url(url: str) -> str:
    return url


class Database:
    def __init__(self, url: str = "sqlite+aiosqlite:///./aite.db") -> None:
        self.url = url
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    @property
    def is_sqlite(self) -> bool:
        return self.url.startswith("sqlite")

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            self._engine = create_async_engine(self.url, echo=False, pool_pre_ping=True)
        return self._engine

    @property
    def sessions(self) -> async_sessionmaker[AsyncSession]:
        if self._session_factory is None:
            self._session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        return self._session_factory

    async def connect(self) -> None:
        if self.is_sqlite:
            from sqlalchemy import event

            @event.listens_for(self.engine.sync_engine, "connect")
            def _set_sqlite_pragma(dbapi_conn, _record):  # pragma: no cover - driver-level
                cursor = dbapi_conn.cursor()
                try:
                    cursor.execute("PRAGMA journal_mode=WAL")
                    cursor.execute("PRAGMA synchronous=NORMAL")
                    cursor.execute("PRAGMA busy_timeout=5000")
                    cursor.execute("PRAGMA foreign_keys=ON")
                finally:
                    cursor.close()

    async def create_all(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def init(self) -> None:
        if self.is_sqlite and ":///" in self.url:
            # ensure parent dir exists for file-based sqlite
            db_file = self.url.split("///")[-1].split("?")[0]
            if db_file and db_file != ":memory:":
                Path(db_file).parent.mkdir(parents=True, exist_ok=True)
        await self.connect()
        await self.create_all()
        from aite.storage import migrations

        await migrations.apply(self.engine)
        logger.debug("database ready: %s", self.url)

    async def dispose(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
