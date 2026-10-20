"""WG_TEST_DATABASE_URL if set, otherwise postgres in testcontainers"""

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from asgi_lifespan import LifespanManager
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.community.postgres import PostgresContainer

from tests.fakes.clock import FakeClock
from tests.fakes.resolver import FakeResolver
from webhook_gateway.api.app import create_app
from webhook_gateway.config import Settings
from webhook_gateway.db import create_sessionmaker
from webhook_gateway.repositories.tables import metadata

ROOT = Path(__file__).resolve().parents[2]


def _server_url() -> Iterator[str]:
    explicit = os.environ.get("WG_TEST_DATABASE_URL")
    if explicit:
        yield explicit
        return
    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as pg:
        yield pg.get_connection_url()


@pytest.fixture(scope="session")
def server_url() -> Iterator[str]:
    yield from _server_url()


@pytest.fixture(scope="session")
async def database_url(server_url: str) -> AsyncIterator[str]:
    name = f"wg_test_{uuid.uuid4().hex[:12]}"
    admin = create_async_engine(server_url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(server_url).set(database=name).render_as_string(hide_password=False)
    try:
        await _migrate(url)
        yield url
    finally:
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await admin.dispose()


async def _migrate(url: str) -> None:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.attributes["configure_logger"] = False
    engine = create_async_engine(url)
    async with engine.begin() as conn:

        def _upgrade(sync_conn: object) -> None:
            cfg.attributes["connection"] = sync_conn
            command.upgrade(cfg, "head")

        await conn.run_sync(_upgrade)
    await engine.dispose()


@pytest.fixture(scope="session")
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(database_url, pool_size=20)
    yield eng
    await eng.dispose()


@pytest.fixture
async def sessionmaker(engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    yield create_sessionmaker(engine)
    tables = ", ".join(t.name for t in metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} CASCADE"))


@pytest.fixture
def settings(database_url: str) -> Settings:
    return Settings(
        _env_file=None,
        database_url=database_url,
        ssrf_allowlist=["127.0.0.1"],
        log_json=False,
    )


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def resolver() -> FakeResolver:
    return FakeResolver(
        {
            "public.example": ["93.184.216.34"],
            "dual.example": ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"],
            "internal.example": ["192.168.1.10"],
        }
    )


@pytest.fixture
async def client(
    settings: Settings,
    clock: FakeClock,
    resolver: FakeResolver,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, clock=clock, resolver=resolver)
    async with LifespanManager(app) as manager:
        transport = httpx.ASGITransport(app=manager.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
