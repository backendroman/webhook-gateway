from datetime import UTC, datetime, timedelta

from webhook_gateway.domain.circuit_breaker import BreakerPolicy, BreakerState, on_attempt
from webhook_gateway.domain.enums import EndpointStatus

NOW = datetime(2026, 1, 1, tzinfo=UTC)
POLICY = BreakerPolicy(threshold=3, cooldown_s=60)
ACTIVE = BreakerState(EndpointStatus.ACTIVE, None, 0)


def _fail(s: BreakerState, now: datetime = NOW) -> BreakerState:
    return on_attempt(s, success=False, now=now, policy=POLICY)


def _ok(s: BreakerState, now: datetime = NOW) -> BreakerState:
    return on_attempt(s, success=True, now=now, policy=POLICY)


def test_opens_after_threshold_consecutive_failures() -> None:
    s = _fail(_fail(ACTIVE))
    assert s == BreakerState(EndpointStatus.ACTIVE, None, 2)
    s = _fail(s)
    assert s.status is EndpointStatus.PAUSED
    assert s.paused_until == NOW + timedelta(seconds=60)
    assert not s.accepts_attempts(NOW + timedelta(seconds=59))
    assert s.accepts_attempts(NOW + timedelta(seconds=60))


def test_success_resets_counter() -> None:
    s = _ok(_fail(_fail(ACTIVE)))
    assert s == ACTIVE
    assert _fail(_fail(s)).status is EndpointStatus.ACTIVE  # count starts over


def test_half_open_failure_reopens_immediately() -> None:
    opened = _fail(_fail(_fail(ACTIVE)))
    later = NOW + timedelta(seconds=61)
    reopened = _fail(opened, later)
    assert reopened.status is EndpointStatus.PAUSED
    assert reopened.paused_until == later + timedelta(seconds=60)


def test_half_open_success_closes() -> None:
    opened = _fail(_fail(_fail(ACTIVE)))
    assert _ok(opened, NOW + timedelta(seconds=61)) == ACTIVE


def test_manual_pause_is_never_lifted_by_attempts() -> None:
    manual = BreakerState(EndpointStatus.PAUSED, None, 0)
    assert manual.manually_paused
    assert not manual.accepts_attempts(NOW + timedelta(days=365))
    assert _ok(manual) == manual
    after_fail = _fail(_fail(_fail(manual)))
    assert after_fail.status is EndpointStatus.PAUSED
    assert after_fail.paused_until is None


def test_active_endpoint_always_accepts() -> None:
    s = BreakerState(EndpointStatus.ACTIVE, None, 4)
    assert s.accepts_attempts(datetime(2000, 1, 1, tzinfo=UTC))
    assert not s.manually_paused
