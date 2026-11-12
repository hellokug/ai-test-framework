"""storage 轻量迁移回归：旧版库缺列时 apply 补列（owner 现有 aite.db 兼容路径）。

TaskLifecycleMixin.history（2026-11-12）走 _PENDING_COLUMNS 注册；新库由
create_all 建全列，apply 幂等跳过。这里用「手工建旧表」复现升级场景。
"""

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from aite.storage import migrations


async def test_apply_adds_history_column(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")
    async with engine.begin() as conn:
        # apply 会逐一 inspect 注册表 → 旧库里五张表都在，缺的只是列
        for table in ("test_run", "functional_task", "accuracy_task",
                      "perf_task", "reliability_task"):
            await conn.execute(sa.text(f"CREATE TABLE {table} (id TEXT PRIMARY KEY)"))
    await migrations.apply(engine)
    async with engine.connect() as conn:
        cols = await conn.run_sync(
            lambda c: {x["name"] for x in sa.inspect(c).get_columns("functional_task")})
    await engine.dispose()
    assert "history" in cols
