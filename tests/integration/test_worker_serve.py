import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes.clock import FakeClock
from tests.fakes.receiver import FakeReceiver, run_receiver
from tests.fakes.sender import CountingSender
from tests.integration.worker_helpers import ALLOW_LOCAL, Harness, worker_config
from webhook_gateway.config import Settings
from webhook_gateway.services.endpoints import EndpointService
from webhook_gateway.worker import main as worker_main
from webhook_gateway.worker.runner import DeliveryWorker

pytestmark = pytest.mark.integration


@pytest.fixture
async def receiver() -> AsyncIterator[FakeReceiver]:
    async with run_receiver() as r:
        yield r


async def test_serve_delivers_and_stops(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    clock: FakeClock,
    receiver: FakeReceiver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receiver.always(200)
    async with httpx.AsyncClient() as http:
        h = Harness(sessionmaker, clock, http)
        await h.endpoint(receiver.url)
        event_id = await h.event()

    # serve() uses the real clock, deliveries created with FakeClock (2026-01-01) are already due
    stops: list[asyncio.Event] = []
    monkeypatch.setattr(worker_main, "install_stop_handlers", stops.append)
    cfg = settings.model_copy(
        update={
            "worker_metrics_port": 0,
            "worker_poll_interval_s": 0.02,
            "worker_stats_interval_s": 0.02,
        }
    )
    task = asyncio.create_task(worker_main.serve(cfg))

    async def delivered() -> None:
        while (await h.delivery_of(event_id)).status != "succeeded":  # noqa: ASYNC110
            await asyncio.sleep(0.02)

    await asyncio.wait_for(delivered(), timeout=10)
    [stop] = stops
    stop.set()
    await asyncio.wait_for(task, timeout=10)
    assert len(receiver.requests) == 1


async def test_endpoint_deleted_while_in_flight(
    sessionmaker: async_sessionmaker[AsyncSession], clock: FakeClock
) -> None:
    async with httpx.AsyncClient() as http:
        h = Harness(sessionmaker, clock, http)
        endpoint_id = await h.endpoint("http://127.0.0.1:9/hook")
        await h.event()
        w = DeliveryWorker(sessionmaker, CountingSender(), clock, worker_config())
        [claimed] = await w.claim(1)
        await EndpointService(sessionmaker, ALLOW_LOCAL, clock).delete(endpoint_id)
        await w.process(claimed)  # delivery is gone with the endpoint, result is dropped
        assert await h.all_deliveries() == []
