import uuid
from collections.abc import AsyncIterator
from datetime import timedelta

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes.clock import FakeClock
from tests.integration.worker_helpers import Harness
from webhook_gateway.domain.enums import DeliveryStatus
from webhook_gateway.domain.errors import NotFoundError
from webhook_gateway.repositories.deliveries import DeliveryFilter, DeliveryRepository
from webhook_gateway.repositories.endpoints import EndpointRepository
from webhook_gateway.repositories.events import EventRepository
from webhook_gateway.repositories.idempotency import IdempotencyRepository
from webhook_gateway.repositories.stats import StatsRepository
from webhook_gateway.services.deliveries import DeliveryService

pytestmark = pytest.mark.integration

URL = "http://127.0.0.1:9/hook"


@pytest.fixture
async def h(
    sessionmaker: async_sessionmaker[AsyncSession], clock: FakeClock
) -> AsyncIterator[Harness]:
    async with httpx.AsyncClient() as client:
        yield Harness(sessionmaker, clock, client)


async def test_event_get(h: Harness) -> None:
    await h.endpoint(URL)
    event_id = await h.event({"k": [1, 2]})
    async with h.sm() as s:
        repo = EventRepository(s)
        event = await repo.get(event_id)
        assert await repo.get(uuid.uuid4()) is None
    assert event is not None
    assert (event.type, event.payload, event.created_at) == ("t", {"k": [1, 2]}, h.clock.now())


async def test_idempotency_get_missing(h: Harness) -> None:
    async with h.sm() as s:
        assert await IdempotencyRepository(s).get("nope") is None


async def test_get_for_update(h: Harness) -> None:
    endpoint_id = await h.endpoint(URL)
    event_id = await h.event()
    d = await h.delivery_of(event_id)
    async with h.sm.begin() as s:
        delivery = await DeliveryRepository(s).get(d.id, for_update=True)
        endpoint = await EndpointRepository(s).get(endpoint_id, for_update=True)
    assert delivery is not None
    assert delivery.status is DeliveryStatus.PENDING
    assert endpoint is not None
    assert endpoint.id == endpoint_id


async def test_missing_rows(h: Harness) -> None:
    missing = uuid.uuid4()
    async with h.sm.begin() as s:
        endpoints = EndpointRepository(s)
        assert await endpoints.lock_breaker(missing) is None
        assert await endpoints.update(missing, h.clock.now(), description="x") is None
        assert await endpoints.delete(missing) is False
        assert await DeliveryRepository(s).requeue(missing, h.clock.now()) is None


async def test_queue_stats(h: Harness) -> None:
    await h.endpoint(URL)
    for i in range(4):
        await h.event({"i": i})
    w = h.worker()
    first, second = await w.claim(2)
    async with h.sm.begin() as s:
        repo = DeliveryRepository(s)
        now = h.clock.now()
        later = now + timedelta(minutes=5)
        await repo.complete(
            delivery_id=first.id,
            lease_token=first.lease_token,
            status=DeliveryStatus.DEAD,
            next_attempt_at=None,
            last_status_code=500,
            last_error=None,
            now=now,
        )
        await repo.complete(
            delivery_id=second.id,
            lease_token=second.lease_token,
            status=DeliveryStatus.PENDING,
            next_attempt_at=later,
            last_status_code=500,
            last_error=None,
            now=now,
        )
    async with h.sm() as s:
        stats = await StatsRepository(s).queue_stats(h.clock.now())
    assert (stats.due, stats.scheduled, stats.in_flight, stats.dead) == (2, 1, 0, 1)


async def test_complete_with_wrong_token_is_rejected(h: Harness) -> None:
    await h.endpoint(URL)
    await h.event()
    [c] = await h.worker().claim(1)
    async with h.sm.begin() as s:
        ok = await DeliveryRepository(s).complete(
            delivery_id=c.id,
            lease_token=uuid.uuid4(),
            status=DeliveryStatus.SUCCEEDED,
            next_attempt_at=None,
            last_status_code=200,
            last_error=None,
            now=h.clock.now(),
        )
    assert ok is False
    assert (await h.delivery_of(c.event_id)).status == "in_flight"


async def test_delivery_service(h: Harness) -> None:
    await h.endpoint(URL)
    await h.event()
    svc = DeliveryService(h.sm, h.clock)

    page = await svc.list(DeliveryFilter(status=DeliveryStatus.PENDING), cursor=None, limit=10)
    assert len(page.items) == 1
    assert page.next_cursor is None
    details = await svc.get(page.items[0].id)
    assert details.attempts == []

    with pytest.raises(NotFoundError):
        await svc.get(uuid.uuid4())
    with pytest.raises(NotFoundError):
        await svc.replay(uuid.uuid4())
