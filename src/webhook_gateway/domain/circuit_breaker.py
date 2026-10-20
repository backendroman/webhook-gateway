from dataclasses import dataclass
from datetime import datetime, timedelta

from webhook_gateway.domain.enums import EndpointStatus


@dataclass(frozen=True, slots=True)
class BreakerPolicy:
    threshold: int
    cooldown_s: float


@dataclass(frozen=True, slots=True)
class BreakerState:
    status: EndpointStatus
    paused_until: datetime | None
    consecutive_failures: int

    # paused without a timer = paused by hand, only PATCH can resume it
    @property
    def manually_paused(self) -> bool:
        return self.status is EndpointStatus.PAUSED and self.paused_until is None

    def accepts_attempts(self, now: datetime) -> bool:
        if self.status is EndpointStatus.ACTIVE:
            return True
        return self.paused_until is not None and self.paused_until <= now


def on_attempt(
    state: BreakerState, *, success: bool, now: datetime, policy: BreakerPolicy
) -> BreakerState:
    if success:
        if state.manually_paused:
            return BreakerState(EndpointStatus.PAUSED, None, 0)
        return BreakerState(EndpointStatus.ACTIVE, None, 0)

    # counter is not reset after cooldown, so in half-open one failure re-opens it
    failures = state.consecutive_failures + 1
    if state.manually_paused:
        return BreakerState(EndpointStatus.PAUSED, None, failures)
    if failures >= policy.threshold:
        return BreakerState(
            EndpointStatus.PAUSED, now + timedelta(seconds=policy.cooldown_s), failures
        )
    return BreakerState(state.status, state.paused_until, failures)
