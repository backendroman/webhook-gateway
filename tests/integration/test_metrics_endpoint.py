import httpx
import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes.clock import FakeClock
from tests.integration.worker_helpers import Harness
from webhook_gateway.repositories.tables import deliveries

pytestmark = pytest.mark.integration


def _sample(text: str, line_prefix: str) -> float:
    for line in text.splitlines():
        if line.startswith(line_prefix):
            return float(line.rsplit(" ", 1)[1])
    raise AssertionError(f"{line_prefix} not found")


async def test_metrics_expose_queue_depth_and_dead(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession], clock: FakeClock
) -> None:
    async with httpx.AsyncClient() as http:
        h = Harness(sessionmaker, clock, http)
        await h.endpoint("http://127.0.0.1:9/a")
        await h.endpoint("http://127.0.0.1:9/b")
        for i in range(3):
            await h.event({"i": i})  # 6 pending, due now
    first = (await h.all_deliveries())[0]
    async with sessionmaker.begin() as s:
        await s.execute(update(deliveries).where(deliveries.c.id == first.id).values(status="dead"))

    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.text
    assert _sample(body, 'webhook_queue_depth{state="due"}') == 5
    assert _sample(body, 'webhook_queue_depth{state="scheduled"}') == 0
    assert _sample(body, "webhook_dead_deliveries ") == 1
    assert "webhook_delivery_attempts_total" in body  # declared (HELP/TYPE)
