"""Report artifacts: re-render stored runs on the fly (html / json / junit)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from aite.core.registry import resolve_renderer
from aite.reporting.rebuild import rebuild_report
from aite.web.deps import get_state

router = APIRouter(tags=["reports"])


@router.get("/runs/{run_id}/report.html")
async def report_html(run_id: str, fmt: str = "html", state: Any = Depends(get_state)):
    if fmt not in ("html", "json", "junit", "rich"):
        raise HTTPException(422, "fmt must be html|json|junit|rich")
    report = await rebuild_report(state.repo, run_id)
    if report is None:
        raise HTTPException(404, "run not found")
    out_dir = Path(tempfile.mkdtemp(prefix="aite-report-"))
    path = resolve_renderer(fmt)().render(report, out_dir)
    media = "application/json" if fmt == "json" else (
        "application/xml" if fmt == "junit" else "text/html")
    if fmt == "rich":
        media = "text/plain"
    return FileResponse(path, media_type=media,
                        filename=path.name)
