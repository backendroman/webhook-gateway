import httpx
import pytest
from asgi_lifespan import LifespanManager

from webhook_gateway.api.app import create_app
from webhook_gateway.config import Settings

pytestmark = pytest.mark.integration


async def test_health(client: httpx.AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_ready_when_db_up(client: httpx.AsyncClient) -> None:
    resp = await client.get("/ready")
    assert resp.status_code == 200
    assert resp.json()["database"] == "up"


async def test_ready_when_db_down() -> None:
    bad = Settings(_env_file=None, database_url="postgresql+asyncpg://nobody:x@127.0.0.1:1/none")
    app = create_app(bad)
    async with LifespanManager(app) as manager:
        transport = httpx.ASGITransport(app=manager.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/ready")
    assert resp.status_code == 503
