import asyncio
import random
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes.clock import FakeClock
from tests.fakes.sender import CountingSender
from tests.integration.worker_helpers import Harness, worker_config
from webhook_gateway.repositories.tables import endpoints
from webhook_gateway.worker.runner import DeliveryWorker
from webhook_gateway.worker.sender import Sender

pytestmark = pytest.mark.integration

URL = "http://127.0.0.1:9/hook"  # fake senders, nobody listens here


@pytest.fixture
async def h(
    sessionmaker: async_sessionmaker[AsyncSession], clock: FakeClock
) -> AsyncIterator[Harness]:
    async with httpx.AsyncClient() as client:
        yield Harness(sessionmaker, clock, client)


def _worker(h: Harness, sender: Sender, **cfg: object) -> DeliveryWorker:
    return DeliveryWorker(h.sm, sender, h.clock, worker_config(**cfg), rng=random.Random(0))


async def test_concurrent_claims_are_disjoint(h: Harness) -> None:
    await h.endpoint(URL)
    for i in range(60):
        await h.event({"i": i})

    batches = await asyncio.gather(*(_worker(h, CountingSender()).claim(7) for _ in range(12)))

    ids = [c.id for batch in batches for c in batch]
    assert len(ids) == 60
    assert len(set(ids)) == 60


async def test_many_workers_deliver_each_event_exactly_once(h: Harness) -> None:
    await h.endpoint(URL)
    event_ids = [await h.event({"i": i}) for i in range(100)]
    sender = CountingSender()
    workers = [_worker(h, sender, batch_size=5, concurrency=3) for _ in range(6)]

    while sum(await asyncio.gather(*(w.run_once() for w in workers))):
        pass

    assert set(sender.calls) == {str(e) for e in event_ids}
    assert set(sender.calls.values()) == {1}
    assert {d.status for d in await h.all_deliveries()} == {"succeeded"}


async def test_in_flight_delivery_is_not_claimed_again_before_lease_expiry(h: Harness) -> None:
    await h.endpoint(URL)
    await h.event()
    w = _worker(h, CountingSender(), lease_s=30.0)

    [claimed] = await w.claim(10)
    h.clock.advance(29)
    await w.recover_expired_leases()
    assert await w.claim(10) == []
    assert claimed.attempt_number == 1


async def test_paused_endpoint_deliveries_are_not_claimed(h: Harness) -> None:
    endpoint_id = await h.endpoint(URL)
    await h.event()
    async with h.sm.begin() as s:
        await s.execute(
            update(endpoints).where(endpoints.c.id == endpoint_id).values(status="paused")
        )
    assert await _worker(h, CountingSender()).claim(10) == []
