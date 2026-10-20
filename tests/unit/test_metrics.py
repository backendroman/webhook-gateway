import asyncio

import pytest

from tests.fakes.clock import FakeClock
from webhook_gateway.domain.enums import AttemptOutcome, DeliveryStatus
from webhook_gateway.metrics import MetricsHook, WorkerMetrics
from webhook_gateway.services.queue_stats import QueueGaugeUpdater


def _value(m: WorkerMetrics, name: str, labels: dict[str, str] | None = None) -> float:
    v = m.registry.get_sample_value(name, labels or {})
    return 0.0 if v is None else v


def test_hook_counts_attempts_latency_and_terminal_statuses() -> None:
    m = WorkerMetrics.create()
    hook = MetricsHook(m)
    hook.attempt_recorded(AttemptOutcome.RETRYABLE, DeliveryStatus.PENDING, 0.2)
    hook.attempt_recorded(AttemptOutcome.SUCCESS, DeliveryStatus.SUCCEEDED, 0.05)
    hook.attempt_recorded(AttemptOutcome.RETRYABLE, DeliveryStatus.DEAD, 0.3)
    hook.lease_expired(DeliveryStatus.DEAD)

    total = "webhook_delivery_attempts_total"
    assert _value(m, total, {"outcome": "retryable"}) == 2
    assert _value(m, total, {"outcome": "success"}) == 1
    assert _value(m, total, {"outcome": "lease_expired"}) == 1
    final = "webhook_delivery_final_status_total"
    assert _value(m, final, {"status": "dead"}) == 2
    assert _value(m, final, {"status": "succeeded"}) == 1
    hist = "webhook_delivery_attempt_duration_seconds_count"
    assert _value(m, hist, {"outcome": "retryable"}) == 2


def test_registries_are_isolated() -> None:
    a, b = WorkerMetrics.create(), WorkerMetrics.create()
    MetricsHook(a).attempt_recorded(AttemptOutcome.SUCCESS, DeliveryStatus.SUCCEEDED, 0.1)
    assert _value(b, "webhook_delivery_attempts_total", {"outcome": "success"}) == 0


async def test_gauge_loop_keeps_going_after_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    updater = QueueGaugeUpdater(None, WorkerMetrics.create(), FakeClock())  # type: ignore[arg-type]
    stop = asyncio.Event()
    calls = 0

    async def flaky_refresh() -> None:
        nonlocal calls
        calls += 1
        if calls == 3:
            stop.set()
        raise ConnectionError("db is down")

    monkeypatch.setattr(updater, "refresh", flaky_refresh)
    await asyncio.wait_for(updater.run(stop, interval_s=0.001), timeout=5)
    assert calls == 3
