"""Shared fixtures: temp sqlite DB + repository."""

from __future__ import annotations

import pytest
import pytest_asyncio

from aite.storage.db import Database
from aite.storage.repositories import SqlAlchemyRepository


@pytest.fixture()
def db_url(tmp_path):
    return f"sqlite+aiosqlite:///{tmp_path/'test.db'}"


@pytest_asyncio.fixture()
async def repo(db_url):
    repository = SqlAlchemyRepository(Database(db_url))
    await repository.init()
    yield repository
    await repository.close()
