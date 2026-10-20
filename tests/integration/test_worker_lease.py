import asyncio
import random
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes.clock import FakeClock
from tests.fakes.sender import CountingSender, GatedSender
from tests.integration.worker_helpers import Harness, worker_config
from webhook_gateway.domain.enums import AttemptOutcome
from webhook_gateway.worker.runner import DeliveryWorker
from webhook_gateway.worker.sender import Sender

pytestmark = pytest.mark.integration

URL = "http://127.0.0.1:9/hook"


@pytest.fixture
async def h(
    sessionmaker: async_sessionmaker[AsyncSession], clock: FakeClock
) -> AsyncIterator[Harness]:
    async with httpx.AsyncClient() as client:
        yield Harness(sessionmaker, clock, client)


def _worker(h: Harness, sender: Sender, **cfg: object) -> DeliveryWorker:
    return DeliveryWorker(h.sm, sender, h.clock, worker_config(**cfg), rng=random.Random(0))


async def test_crashed_worker_lease_expires_and_delivery_is_retried(h: Harness) -> None:
    await h.endpoint(URL)
    event_id = await h.event()
    crashed = _worker(h, CountingSender(), lease_s=30.0)

    # claims and dies
    [claimed] = await crashed.claim(10)
    d = await h.delivery_of(event_id)
    assert (d.status, d.attempt_count) == ("in_flight", 1)

    h.clock.advance(31)
    survivor_sender = CountingSender()
    assert await _worker(h, survivor_sender).run_once() == 1

    d = await h.delivery_of(event_id)
    assert (d.status, d.attempt_count) == ("succeeded", 2)
    history = [(a.attempt_number, a.outcome) for a in await h.attempts(d.id)]
    assert history == [(1, AttemptOutcome.LEASE_EXPIRED), (2, AttemptOutcome.SUCCESS)]
    assert survivor_sender.calls[str(event_id)] == 1

    # zombie wakes up, its result must be dropped
    await crashed.process(claimed)
    d = await h.delivery_of(event_id)
    assert (d.status, d.attempt_count) == ("succeeded", 2)
    assert len(await h.attempts(d.id)) == 2


async def test_lease_expiry_on_last_attempt_goes_dead(h: Harness) -> None:
    await h.endpoint(URL)
    event_id = await h.event()
    w = _worker(h, CountingSender(), lease_s=30.0)  # max_attempts=3
    for _ in range(3):
        assert len(await w.claim(10)) == 1
        h.clock.advance(31)
        await w.recover_expired_leases()

    d = await h.delivery_of(event_id)
    assert (d.status, d.attempt_count) == ("dead", 3)
    assert [a.outcome for a in await h.attempts(d.id)] == [AttemptOutcome.LEASE_EXPIRED] * 3


async def test_graceful_shutdown_finishes_in_flight_and_claims_nothing_new(h: Harness) -> None:
    await h.endpoint(URL)
    for i in range(3):
        await h.event({"i": i})
    sender = GatedSender()
    worker = _worker(h, sender, batch_size=1, concurrency=1, poll_interval_s=0.01)
    stop = asyncio.Event()

    run = asyncio.create_task(worker.run(stop))
    await asyncio.wait_for(sender.started.wait(), timeout=5)
    stop.set()
    await asyncio.sleep(0.05)
    assert not run.done()
    sender.release.set()
    await asyncio.wait_for(run, timeout=5)

    statuses = sorted(d.status for d in await h.all_deliveries())
    assert statuses == ["pending", "pending", "succeeded"]
    assert (sender.completed, sender.in_progress) == (1, 0)


async def test_shutdown_grace_timeout_abandons_attempt_to_lease_recovery(h: Harness) -> None:
    await h.endpoint(URL)
    event_id = await h.event()
    sender = GatedSender()  # never released
    worker = _worker(h, sender, concurrency=1, poll_interval_s=0.01, shutdown_grace_s=0.1)
    stop = asyncio.Event()

    run = asyncio.create_task(worker.run(stop))
    await asyncio.wait_for(sender.started.wait(), timeout=5)
    stop.set()
    await asyncio.wait_for(run, timeout=5)

    d = await h.delivery_of(event_id)
    assert d.status == "in_flight"
    h.clock.advance(31)
    assert await _worker(h, CountingSender()).run_once() == 1
    assert (await h.delivery_of(event_id)).status == "succeeded"
