import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

from webhook_gateway.domain.classification import honors_retry_after
from webhook_gateway.domain.enums import AttemptOutcome, DeliveryStatus


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int
    base_s: float
    cap_s: float

    def backoff(self, attempt: int, rng: random.Random) -> float:
        # equal jitter: uniform in [c/2, c], c = min(cap, base * 2^(attempt-1))
        if attempt < 1:
            raise ValueError("attempt is 1-based")
        exponent = min(attempt - 1, 62)  # no float overflow on huge numbers
        ceiling = min(self.cap_s, self.base_s * (2.0**exponent))
        half = ceiling / 2.0
        return half + rng.uniform(0.0, half)


def parse_retry_after(value: str | None, now: datetime) -> float | None:
    # seconds or HTTP-date, None if missing or garbage
    if value is None:
        return None
    value = value.strip()
    if value.isdigit():
        return float(int(value))
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        return None
    return max(0.0, (when - now).total_seconds())


@dataclass(frozen=True, slots=True)
class NextStep:
    status: DeliveryStatus
    next_attempt_at: datetime | None  # only for PENDING


def plan_next(
    *,
    outcome: AttemptOutcome,
    attempts_used: int,
    status_code: int | None,
    retry_after_s: float | None,
    now: datetime,
    policy: RetryPolicy,
    rng: random.Random,
) -> NextStep:
    if outcome is AttemptOutcome.SUCCESS:
        return NextStep(DeliveryStatus.SUCCEEDED, None)
    if outcome is AttemptOutcome.PERMANENT:
        return NextStep(DeliveryStatus.FAILED, None)
    if attempts_used >= policy.max_attempts:
        return NextStep(DeliveryStatus.DEAD, None)

    delay = policy.backoff(attempts_used, rng)
    if retry_after_s is not None and honors_retry_after(status_code):
        # capped so a receiver can't park a delivery forever
        delay = max(delay, min(retry_after_s, policy.cap_s))
    return NextStep(DeliveryStatus.PENDING, now + timedelta(seconds=delay))
