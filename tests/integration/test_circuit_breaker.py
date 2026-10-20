from collections.abc import AsyncIterator
from uuid import UUID

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes.clock import FakeClock
from tests.fakes.receiver import FakeReceiver, run_receiver
from tests.integration.worker_helpers import ALLOW_LOCAL, Harness
from webhook_gateway.domain.circuit_breaker import BreakerPolicy
from webhook_gateway.domain.enums import EndpointStatus
from webhook_gateway.domain.retry import RetryPolicy
from webhook_gateway.repositories.endpoints import EndpointRepository
from webhook_gateway.services.endpoints import EndpointPatch, EndpointService
from webhook_gateway.worker.runner import DeliveryWorker

pytestmark = pytest.mark.integration

COOLDOWN = 300.0


@pytest.fixture
async def receiver() -> AsyncIterator[FakeReceiver]:
    async with run_receiver() as r:
        yield r


@pytest.fixture
async def h(
    sessionmaker: async_sessionmaker[AsyncSession], clock: FakeClock
) -> AsyncIterator[Harness]:
    async with httpx.AsyncClient(timeout=5.0) as client:
        yield Harness(sessionmaker, clock, client)


def _worker(h: Harness) -> DeliveryWorker:
    return h.worker(
        batch_size=50,
        retry=RetryPolicy(max_attempts=10, base_s=1.0, cap_s=1.0),
        breaker=BreakerPolicy(threshold=3, cooldown_s=COOLDOWN),
    )


async def _status(h: Harness, endpoint_id: UUID) -> tuple[EndpointStatus, int]:
    async with h.sm() as s:
        e = await EndpointRepository(s).get(endpoint_id)
    assert e is not None
    return e.status, e.consecutive_failures


def _endpoints(h: Harness) -> EndpointService:
    return EndpointService(h.sm, ALLOW_LOCAL, h.clock)


async def test_breaker_opens_blocks_and_closes_by_timer(h: Harness, receiver: FakeReceiver) -> None:
    receiver.always(500)
    endpoint_id = await h.endpoint(receiver.url)
    for i in range(3):
        await h.event({"i": i})
    worker = _worker(h)

    await worker.run_once()  # 3 deliveries fail -> 3 consecutive failures
    assert await _status(h, endpoint_id) == (EndpointStatus.PAUSED, 3)
    requests_when_opened = len(receiver.requests)

    # open: nothing is picked, attempts are not spent
    h.clock.advance(COOLDOWN - 1)
    assert await worker.run_once() == 0
    assert len(receiver.requests) == requests_when_opened

    # half-open
    receiver.always(200)
    h.clock.advance(1)
    assert await worker.run_once() == 3
    assert await _status(h, endpoint_id) == (EndpointStatus.ACTIVE, 0)
    assert {d.status for d in await h.all_deliveries()} == {"succeeded"}


async def test_half_open_failure_reopens(h: Harness, receiver: FakeReceiver) -> None:
    receiver.always(500)
    endpoint_id = await h.endpoint(receiver.url)
    for i in range(3):
        await h.event({"i": i})
    worker = _worker(h)
    await worker.run_once()
    assert (await _status(h, endpoint_id))[0] is EndpointStatus.PAUSED

    h.clock.advance(COOLDOWN)
    await worker.run_once()  # still failing
    status, failures = await _status(h, endpoint_id)
    assert status is EndpointStatus.PAUSED
    assert failures == 6
    h.clock.advance(COOLDOWN - 1)
    assert await worker.run_once() == 0  # paused again for a full cooldown


async def test_manual_resume_lifts_breaker(h: Harness, receiver: FakeReceiver) -> None:
    receiver.always(503)
    endpoint_id = await h.endpoint(receiver.url)
    for i in range(3):
        await h.event({"i": i})
    worker = _worker(h)
    await worker.run_once()
    assert (await _status(h, endpoint_id))[0] is EndpointStatus.PAUSED

    await _endpoints(h).update(endpoint_id, EndpointPatch(status=EndpointStatus.ACTIVE))
    assert await _status(h, endpoint_id) == (EndpointStatus.ACTIVE, 0)

    receiver.always(200)
    h.clock.advance(2)  # past the 1s backoff, far before the breaker timer
    assert await worker.run_once() == 3


async def test_manual_pause_holds_deliveries_until_resumed(
    h: Harness, receiver: FakeReceiver
) -> None:
    receiver.always(200)
    endpoint_id = await h.endpoint(receiver.url)
    svc = _endpoints(h)
    await svc.update(endpoint_id, EndpointPatch(status=EndpointStatus.PAUSED))
    await h.event()
    worker = _worker(h)

    h.clock.advance(10 * COOLDOWN)
    assert await worker.run_once() == 0  # no timer: stays paused
    await svc.update(endpoint_id, EndpointPatch(status=EndpointStatus.ACTIVE))
    assert await worker.run_once() == 1
