import random
from datetime import UTC, datetime, timedelta

import pytest

from webhook_gateway.domain.enums import AttemptOutcome, DeliveryStatus
from webhook_gateway.domain.retry import RetryPolicy, parse_retry_after, plan_next

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
POLICY = RetryPolicy(max_attempts=5, base_s=2.0, cap_s=60.0)


@pytest.mark.parametrize(
    ("attempt", "ceiling"), [(1, 2.0), (2, 4.0), (3, 8.0), (4, 16.0), (5, 32.0), (6, 60.0)]
)
def test_backoff_is_within_equal_jitter_bounds(attempt: int, ceiling: float) -> None:
    rng = random.Random(1)
    delays = [POLICY.backoff(attempt, rng) for _ in range(500)]
    assert all(ceiling / 2 <= d <= ceiling for d in delays)
    assert max(delays) - min(delays) > ceiling / 4


def test_backoff_grows_exponentially_on_average() -> None:
    rng = random.Random(7)
    means = [sum(POLICY.backoff(a, rng) for _ in range(500)) / 500 for a in (1, 2, 3, 4)]
    assert means == sorted(means)
    assert means[3] / means[0] == pytest.approx(8.0, rel=0.15)


def test_backoff_is_capped_even_for_huge_attempt_numbers() -> None:
    rng = random.Random(3)
    assert POLICY.backoff(10_000, rng) <= POLICY.cap_s


def test_backoff_rejects_zero_attempt() -> None:
    with pytest.raises(ValueError, match="1-based"):
        POLICY.backoff(0, random.Random())


def test_backoff_is_deterministic_for_seeded_rng() -> None:
    assert POLICY.backoff(3, random.Random(42)) == POLICY.backoff(3, random.Random(42))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("120", 120.0),
        (" 5 ", 5.0),
        ("0", 0.0),
        ("Thu, 01 Jan 2026 12:01:30 GMT", 90.0),
        ("Thu, 01 Jan 2026 11:00:00 GMT", 0.0),  # past
        (None, None),
        ("", None),
        ("-5", None),
        ("1.5", None),
        ("soon", None),
    ],
)
def test_parse_retry_after(value: str | None, expected: float | None) -> None:
    assert parse_retry_after(value, NOW) == expected


def _plan(
    outcome: AttemptOutcome,
    attempts_used: int = 1,
    status_code: int | None = None,
    retry_after_s: float | None = None,
) -> tuple[DeliveryStatus, float | None]:
    step = plan_next(
        outcome=outcome,
        attempts_used=attempts_used,
        status_code=status_code,
        retry_after_s=retry_after_s,
        now=NOW,
        policy=POLICY,
        rng=random.Random(0),
    )
    delay = (step.next_attempt_at - NOW).total_seconds() if step.next_attempt_at else None
    return step.status, delay


def test_plan_success_and_permanent() -> None:
    assert _plan(AttemptOutcome.SUCCESS) == (DeliveryStatus.SUCCEEDED, None)
    assert _plan(AttemptOutcome.PERMANENT, status_code=404) == (DeliveryStatus.FAILED, None)


@pytest.mark.parametrize(
    "outcome",
    [
        AttemptOutcome.RETRYABLE,
        AttemptOutcome.TIMEOUT,
        AttemptOutcome.NETWORK_ERROR,
        AttemptOutcome.BLOCKED,
    ],
)
def test_plan_retries_until_budget_exhausted(outcome: AttemptOutcome) -> None:
    for used in range(1, POLICY.max_attempts):
        status, delay = _plan(outcome, attempts_used=used)
        assert status is DeliveryStatus.PENDING
        assert delay is not None
        assert delay > 0
    assert _plan(outcome, attempts_used=POLICY.max_attempts) == (DeliveryStatus.DEAD, None)


@pytest.mark.parametrize("code", [429, 503])
def test_plan_honors_retry_after_for_429_and_503(code: int) -> None:
    _, delay = _plan(AttemptOutcome.RETRYABLE, status_code=code, retry_after_s=45.0)
    assert delay == 45.0


def test_plan_retry_after_never_shortens_backoff() -> None:
    _, delay = _plan(AttemptOutcome.RETRYABLE, attempts_used=4, status_code=429, retry_after_s=0)
    assert delay is not None
    assert delay >= 8.0


def test_plan_retry_after_is_capped() -> None:
    _, delay = _plan(AttemptOutcome.RETRYABLE, status_code=503, retry_after_s=10_000.0)
    assert delay == POLICY.cap_s


def test_plan_ignores_retry_after_for_other_statuses() -> None:
    _, delay = _plan(AttemptOutcome.RETRYABLE, status_code=500, retry_after_s=45.0)
    assert delay is not None
    assert delay <= 2.0


def test_plan_dead_even_with_retry_after() -> None:
    status, _ = _plan(AttemptOutcome.RETRYABLE, attempts_used=5, status_code=429, retry_after_s=1.0)
    assert status is DeliveryStatus.DEAD


def test_next_attempt_is_relative_to_now() -> None:
    _, delay = _plan(AttemptOutcome.TIMEOUT)
    assert delay is not None
    assert timedelta(seconds=delay) <= timedelta(seconds=POLICY.base_s)


@pytest.mark.parametrize("value", ["Thu, 01 Jan 2026 12:01:30", "Thu, 01 Jan 2026 12:01:30 -0000"])
def test_retry_after_date_without_tz_is_ignored(value: str) -> None:
    assert parse_retry_after(value, datetime(2026, 1, 1, 12, tzinfo=UTC)) is None
