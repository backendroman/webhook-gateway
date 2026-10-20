import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes.clock import FakeClock
from webhook_gateway.config import Settings
from webhook_gateway.domain.models import ClaimedDelivery
from webhook_gateway.domain.signing import verify_signature
from webhook_gateway.worker.runner import (
    DeliveryWorker,
    WorkerConfig,
    _wait_any,
    build_body,
    build_headers,
)
from webhook_gateway.worker.sender import SendResult

NOW = datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)


def _claimed(**kw: Any) -> ClaimedDelivery:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "event_id": uuid.uuid4(),
        "endpoint_id": uuid.uuid4(),
        "attempt_number": 1,
        "attempts_in_window": 1,
        "lease_token": uuid.uuid4(),
        "url": "https://public.example/h",
        "secret": "whsec_x",
        "event_type": "order.created",
        "payload": {"id": 1, "name": "заказ"},
        "event_created_at": NOW,
    }
    base.update(kw)
    return ClaimedDelivery(**base)


def test_body_envelope() -> None:
    c = _claimed()
    body = build_body(c)
    assert json.loads(body) == {
        "id": str(c.event_id),
        "type": "order.created",
        "created_at": NOW.isoformat(),
        "data": {"id": 1, "name": "заказ"},
    }
    assert "заказ".encode() in body  # no \u escapes
    assert b" " not in body


def test_body_without_created_at() -> None:
    assert json.loads(build_body(_claimed(event_created_at=None)))["created_at"] is None


def test_headers_are_signed() -> None:
    c = _claimed()
    body = build_body(c)
    h = build_headers(c, body, NOW)
    ts = int(NOW.timestamp())
    assert h["X-Webhook-Id"] == str(c.event_id)
    assert h["X-Webhook-Timestamp"] == str(ts)
    assert verify_signature("whsec_x", ts, body, h["X-Webhook-Signature"], now=ts)


def test_config_from_settings() -> None:
    s = Settings(
        _env_file=None,
        worker_batch_size=7,
        worker_concurrency=3,
        delivery_max_attempts=4,
        backoff_base_s=1.5,
        backoff_max_s=90,
        breaker_failure_threshold=2,
        breaker_cooldown_s=10,
    )
    cfg = WorkerConfig.from_settings(s)
    assert (cfg.batch_size, cfg.concurrency) == (7, 3)
    assert (cfg.retry.max_attempts, cfg.retry.base_s, cfg.retry.cap_s) == (4, 1.5, 90)
    assert (cfg.breaker.threshold, cfg.breaker.cooldown_s) == (2, 10)
    assert cfg.lease_s == s.worker_lease_s


# sessionmaker stand-in, every transaction fails
class _BrokenDb:
    def __init__(self, on_call: asyncio.Event | None = None) -> None:
        self.calls = 0
        self._on_call = on_call

    def begin(self) -> Any:
        self.calls += 1
        if self._on_call is not None:
            self._on_call.set()
        raise ConnectionError("db is down")


class _RaisingSender:
    async def send(self, url: str, headers: dict[str, str], body: bytes) -> SendResult:
        raise RuntimeError("bug in sender")


def _worker(db: Any) -> DeliveryWorker:
    cfg = WorkerConfig.from_settings(Settings(_env_file=None, worker_poll_interval_s=0.01))
    return DeliveryWorker(
        cast(async_sessionmaker[AsyncSession], db), _RaisingSender(), FakeClock(), cfg
    )


async def test_loop_survives_db_errors() -> None:
    stop = asyncio.Event()
    db = _BrokenDb(on_call=stop)  # stop after the first failed poll
    await asyncio.wait_for(_worker(db).run(stop), timeout=5)
    assert db.calls == 1


async def test_process_swallows_unexpected_errors() -> None:
    db = _BrokenDb()
    await _worker(db).process(_claimed())
    assert db.calls == 0  # nothing was written, lease will expire


async def test_wait_any_returns_on_event() -> None:
    a, b = asyncio.Event(), asyncio.Event()
    asyncio.get_running_loop().call_later(0.01, b.set)
    await asyncio.wait_for(_wait_any(a, b, max_wait_s=10), timeout=2)


async def test_wait_any_times_out() -> None:
    await asyncio.wait_for(_wait_any(asyncio.Event(), max_wait_s=0.01), timeout=2)
