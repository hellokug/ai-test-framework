"""Lightweight schema migrations (column adds) - keeps pre-existing DBs loadable
without pulling in Alembic. Tracked in ``schema_version`` (key/value row in
``bg_task``-independent table would be overkill; version kept in-memory safe).

Current schema is fully created by ``create_all``; this module only patches
databases created by older aite versions.
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger("aite.storage.migrations")

# (table, column, ddl-fragment)
_PENDING_COLUMNS: list[tuple[str, sa.Column]] = [
    ("test_run", sa.Column("details", sa.JSON(), nullable=True)),
    # 执行历史列（四域任务表同构，TaskLifecycleMixin.history，2026-11-12）
    ("functional_task", sa.Column("history", sa.JSON(), nullable=True)),
    ("accuracy_task", sa.Column("history", sa.JSON(), nullable=True)),
    ("perf_task", sa.Column("history", sa.JSON(), nullable=True)),
    ("reliability_task", sa.Column("history", sa.JSON(), nullable=True)),
]


async def apply(engine: AsyncEngine) -> None:
    if not _PENDING_COLUMNS:
        return
    async with engine.connect() as conn:
        for table, column in _PENDING_COLUMNS:

            def _existing_columns(sync_conn, _t=table) -> set[str]:
                return {c["name"] for c in sa.inspect(sync_conn).get_columns(_t)}

            existing = await conn.run_sync(_existing_columns)
            if column.name not in existing:
                logger.info("migrating: adding %s.%s", table, column.name)
                await conn.execute(
                    sa.text(f"ALTER TABLE {table} ADD COLUMN {column.name} {_ddl_type(column)}")
                )


def _ddl_type(column: sa.Column) -> str:
    return "TEXT"
