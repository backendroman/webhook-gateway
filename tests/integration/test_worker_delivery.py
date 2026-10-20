import asyncio
import json
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes.clock import FakeClock
from tests.fakes.receiver import FakeReceiver, run_receiver
from tests.integration.worker_helpers import Harness
from webhook_gateway.domain.enums import AttemptOutcome
from webhook_gateway.domain.signing import verify_signature

pytestmark = pytest.mark.integration


@pytest.fixture
async def receiver() -> AsyncIterator[FakeReceiver]:
    async with run_receiver() as r:
        yield r


@pytest.fixture
async def h(
    sessionmaker: async_sessionmaker[AsyncSession], clock: FakeClock
) -> AsyncIterator[Harness]:
    async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
        yield Harness(sessionmaker, clock, client)


async def test_always_200_delivers_signed_request(h: Harness, receiver: FakeReceiver) -> None:
    receiver.always(200)
    endpoint_id = await h.endpoint(receiver.url)
    event_id = await h.event({"order": 7})

    assert await h.worker().run_once() == 1

    d = await h.delivery_of(event_id)
    assert d.status == "succeeded"
    assert d.attempt_count == 1
    assert d.locked_until is None
    assert d.lease_token is None

    [req] = receiver.requests
    assert req.headers["content-type"] == "application/json"
    assert req.headers["x-webhook-id"] == str(event_id)
    body = json.loads(req.body)
    assert body["id"] == str(event_id)
    assert body["data"] == {"order": 7}

    secret = await h.secret_of(endpoint_id)
    ts = int(req.headers["x-webhook-timestamp"])
    assert ts == int(h.clock.now().timestamp())
    assert verify_signature(secret, ts, req.body, req.headers["x-webhook-signature"], now=ts)

    [attempt] = await h.attempts(d.id)
    assert attempt.outcome is AttemptOutcome.SUCCESS
    assert attempt.status_code == 200
    assert attempt.response_body == "ok"


async def test_500_twice_then_200_retries_with_backoff(h: Harness, receiver: FakeReceiver) -> None:
    receiver.fail_then(500, times=2, then=200)
    await h.endpoint(receiver.url)
    event_id = await h.event()
    worker = h.worker()

    await worker.run_once()
    d = await h.delivery_of(event_id)
    assert (d.status, d.attempt_count, d.last_status_code) == ("pending", 1, 500)
    delay = (d.next_attempt_at - h.clock.now()).total_seconds()
    assert 5.0 <= delay <= 10.0  # base 10s, equal jitter

    assert await worker.run_once() == 0  # not due yet: nothing happens
    h.clock.advance(delay)
    await worker.run_once()
    d = await h.delivery_of(event_id)
    assert (d.status, d.attempt_count) == ("pending", 2)

    h.clock.advance(20)
    await worker.run_once()
    d = await h.delivery_of(event_id)
    assert (d.status, d.attempt_count) == ("succeeded", 3)
    outcomes = [a.outcome for a in await h.attempts(d.id)]
    assert outcomes == [AttemptOutcome.RETRYABLE, AttemptOutcome.RETRYABLE, AttemptOutcome.SUCCESS]
    assert len(receiver.requests) == 3
    assert len({r.headers["x-webhook-id"] for r in receiver.requests}) == 1


async def test_timeout_is_recorded_and_retried(h: Harness, receiver: FakeReceiver) -> None:
    receiver.hang()
    await h.endpoint(receiver.url)
    event_id = await h.event()

    await h.worker(total_timeout_s=0.3).run_once()

    d = await h.delivery_of(event_id)
    assert d.status == "pending"
    assert d.last_error == "timed out"
    [attempt] = await h.attempts(d.id)
    assert attempt.outcome is AttemptOutcome.TIMEOUT
    assert attempt.status_code is None


async def test_429_with_retry_after_is_respected(h: Harness, receiver: FakeReceiver) -> None:
    receiver.retry_after(429, "75")
    await h.endpoint(receiver.url)
    event_id = await h.event()

    await h.worker().run_once()

    d = await h.delivery_of(event_id)
    assert (d.status, d.last_status_code) == ("pending", 429)
    assert (d.next_attempt_at - h.clock.now()).total_seconds() == 75.0


async def test_non_retryable_4xx_fails_immediately(h: Harness, receiver: FakeReceiver) -> None:
    receiver.always(410)
    await h.endpoint(receiver.url)
    event_id = await h.event()

    await h.worker().run_once()

    d = await h.delivery_of(event_id)
    assert (d.status, d.attempt_count) == ("failed", 1)
    h.clock.advance(10_000)
    assert await h.worker().run_once() == 0


async def test_retries_exhausted_goes_dead(h: Harness, receiver: FakeReceiver) -> None:
    receiver.always(503)
    await h.endpoint(receiver.url)
    event_id = await h.event()
    worker = h.worker()  # max_attempts=3

    for _ in range(3):
        await worker.run_once()
        h.clock.advance(1000)

    d = await h.delivery_of(event_id)
    assert (d.status, d.attempt_count) == ("dead", 3)
    assert await worker.run_once() == 0
    assert len(receiver.requests) == 3


async def test_response_body_is_truncated(h: Harness, receiver: FakeReceiver) -> None:
    receiver.always(200, body=b"x" * 5000)
    await h.endpoint(receiver.url)
    event_id = await h.event()

    await h.worker().run_once()

    d = await h.delivery_of(event_id)
    [attempt] = await h.attempts(d.id)
    assert attempt.response_body == "x" * 64  # stored_response_bytes


async def test_dropped_connection_is_network_error(h: Harness) -> None:
    # closed port won't do: on windows connect refused hangs ~2s and ends up as a timeout
    async def _drop(_r: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.close()

    server = await asyncio.start_server(_drop, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    await h.endpoint(f"http://127.0.0.1:{port}/hook")
    event_id = await h.event()

    async with server:
        await h.worker().run_once()

    d = await h.delivery_of(event_id)
    assert d.status == "pending"
    [attempt] = await h.attempts(d.id)
    assert attempt.outcome is AttemptOutcome.NETWORK_ERROR
