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
    # future: ("test_run", sa.Column("web_url", sa.String(1024), nullable=True)),
]


async def apply(engine: AsyncEngine) -> None:
    if not _PENDING_COLUMNS:
        return
    inspector = sa.inspect(engine.sync_engine)
    for table, column in _PENDING_COLUMNS:
        existing = {c["name"] for c in inspector.get_columns(table)}
        if column.name not in existing:
            logger.info("migrating: adding %s.%s", table, column.name)
            async with engine.begin() as conn:
                await conn.execute(
                    sa.text(f"ALTER TABLE {table} ADD COLUMN {column.name} {_ddl_type(column)}")
                )


def _ddl_type(column: sa.Column) -> str:
    return "TEXT"
