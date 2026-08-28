"""Shared FastAPI dependencies."""

from __future__ import annotations

from fastapi import HTTPException, Request


def get_state(request: Request):
    return request.app.state.aite


async def auth_guard(request: Request) -> None:
    """Optional bearer-token gate (enabled when AITE_WEB_TOKEN is set)."""
    state = request.app.state.aite
    if not state.token:
        return
    if request.url.path == "/api/v1/health":
        return
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {state.token}":
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")
