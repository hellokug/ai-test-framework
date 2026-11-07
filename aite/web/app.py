"""FastAPI application factory (thin shell over Core API)."""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from aite import __version__ as aite_version
from aite import config
from aite.service import close_interrupted_runs
from aite.service.accuracy_tasks import build_accuracy_task_kit
from aite.service.functional_tasks import build_functional_task_kit
from aite.service.perf_tasks import build_perf_task_kit
from aite.service.reliability_tasks import build_reliability_task_kit
from aite.storage.db import Database
from aite.storage.repositories import SqlAlchemyRepository
from aite.utils.logging import get_logger
from aite.web.deps import auth_guard, get_state
from aite.web.events import EventBus

logger = get_logger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

__all__ = ["create_app", "AppState", "get_state"]


class AppState:
    """Shared services for all routers (single uvicorn process)."""

    def __init__(self, storage_url: str, token: str | None) -> None:
        self.db = Database(storage_url)
        self.repo = SqlAlchemyRepository(self.db)
        self.bus = EventBus()
        self.token = token
        self.tasks: dict[str, asyncio.Task] = {}
        # 四域任务 kit（docs/domain-backends.md §2）：bus 共享 → SSE 频道同源
        self.task_kits = {
            "functional": build_functional_task_kit(self.bus),
            "accuracy": build_accuracy_task_kit(self.bus),
            "perf": build_perf_task_kit(self.bus),
            "reliability": build_reliability_task_kit(self.bus),
        }

    async def startup(self) -> None:
        await self.repo.init()
        # 状态机闭合：上次进程崩溃/强杀残留的中间态任务标记为 error
        stale = await self.repo.mark_stale_bg_tasks()
        interrupted = await close_interrupted_runs(self.repo)
        # 四域任务状态机收敛（原则 8）：running/stopping 残留 → stale
        stale_domain = 0
        for kit in self.task_kits.values():
            stale_domain += await kit.mark_stale(self.repo)
        if stale or interrupted or stale_domain:
            logger.warning("启动收敛：%d 个残留后台任务 + %d 个中断 run + "
                           "%d 个域任务标记为 error/stale",
                           stale, interrupted, stale_domain)

    async def shutdown(self) -> None:
        bg_ids = list(self.tasks)
        for task in self.tasks.values():
            task.cancel()
        if bg_ids:
            await asyncio.gather(*(self.tasks[t] for t in bg_ids if t in self.tasks),
                                 return_exceptions=True)
        # worker 的 except Exception 不兜 CancelledError，DB 终态在此显式回写
        for bg_id in bg_ids:
            with contextlib.suppress(Exception):   # shutdown 尽力回写，不阻断关闭
                await self.repo.update_bg_task(bg_id, status="error",
                                               error="server shutdown")
        await self.repo.close()


def create_app(storage_url: str = config.DEFAULT_DB_URL,
               token: str | None = None) -> FastAPI:
    state = AppState(storage_url, token)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await state.startup()
        yield
        await state.shutdown()

    app = FastAPI(
        title="aite", version=aite_version,
        description="AI Test & Evaluation Framework",
        lifespan=lifespan,
        dependencies=[Depends(auth_guard)],
    )
    app.state.aite = state

    from aite.web.routers import (
        accuracy,
        agents,
        cases,
        chaos,
        common,
        datasets,
        domain_tasks,
        functional,
        manual_eval,
        models,
        perfdata,
        reports,
        runs,
        suites,
        tasks,
        testcases,
        tools,
        trends,
    )

    for router in (runs, suites, cases, models, trends, reports, manual_eval,
                   tasks, tools, agents, perfdata, chaos, testcases,
                   datasets, domain_tasks, functional, common, accuracy):
        app.include_router(router.router, prefix="/api/v1")

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

        @app.get("/", include_in_schema=False)
        async def index():
            return FileResponse(STATIC_DIR / "index.html")

    @app.exception_handler(Exception)
    async def unhandled(_request: Request, exc: Exception):  # pragma: no cover
        return JSONResponse(status_code=500, content={"error": str(exc)})

    return app
