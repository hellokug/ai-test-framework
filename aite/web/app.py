"""FastAPI application factory (thin shell over Core API)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from aite.storage.db import Database
from aite.storage.repositories import SqlAlchemyRepository
from aite.web.deps import auth_guard, get_state
from aite.web.events import EventBus

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

    async def startup(self) -> None:
        await self.repo.init()

    async def shutdown(self) -> None:
        for task in self.tasks.values():
            task.cancel()
        await self.repo.close()


def create_app(storage_url: str = "sqlite+aiosqlite:///./aite.db",
               token: str | None = None) -> FastAPI:
    state = AppState(storage_url, token)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await state.startup()
        yield
        await state.shutdown()

    app = FastAPI(
        title="aite", version="0.1.0",
        description="AI Test & Evaluation Framework",
        lifespan=lifespan,
        dependencies=[Depends(auth_guard)],
    )
    app.state.aite = state

    from aite.web.routers import (
        cases,
        manual_eval,
        models,
        reports,
        runs,
        suites,
        tasks,
        tools,
        trends,
    )

    for router in (runs, suites, cases, models, trends, reports, manual_eval,
                   tasks, tools):
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
