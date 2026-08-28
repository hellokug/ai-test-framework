"""Test case management: browse / import JSONL."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from aite.core.types import TestCase
from aite.web.deps import get_state

router = APIRouter(tags=["cases"])


class CaseIn(BaseModel):
    id: str
    input: Any
    expected: Any = None
    metadata: dict[str, Any] = {}
    retrieval_context: list[str] | None = None
    suite: str  # target suite name


class ImportRequest(BaseModel):
    suite: str
    jsonl: str                     # raw JSONL text
    fields: dict[str, str] = {}


@router.get("/cases")
async def list_cases(suite: str, state: Any = Depends(get_state), limit: int = 200):
    found = await state.repo.get_suite_by_name(suite)
    if not found:
        raise HTTPException(404, "suite not found")
    items = await state.repo.list_test_cases(found["id"], limit=limit)
    return {"items": items, "count": len(items)}


@router.post("/cases")
async def add_case(body: CaseIn, state: Any = Depends(get_state)):
    suite_id = await state.repo.upsert_suite(body.suite)
    row_id = await state.repo.save_test_case(suite_id, TestCase(
        id=body.id, input=body.input, expected=body.expected,
        metadata=body.metadata, retrieval_context=body.retrieval_context))
    return {"id": row_id}


@router.post("/cases/import")
async def import_cases(body: ImportRequest, state: Any = Depends(get_state)):
    suite_id = await state.repo.upsert_suite(body.suite)
    from aite.core.loader import DatasetConfig
    from aite.datasets.local import LocalFileDataset

    records = []
    for line in body.jsonl.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise HTTPException(422, f"invalid JSONL line: {exc}") from exc
    config = DatasetConfig(type="local", path="uploaded.jsonl", fields=body.fields)
    loader = LocalFileDataset()
    cases = loader._to_cases(records, config, "uploaded")
    for case in cases:
        case.dataset = "uploaded"
        await state.repo.save_test_case(suite_id, case)
    return {"imported": len(cases)}
