from typing import Protocol

from webhook_gateway.domain.enums import AttemptOutcome, DeliveryStatus


class AttemptHook(Protocol):
    def attempt_recorded(
        self, outcome: AttemptOutcome, new_status: DeliveryStatus, duration_s: float
    ) -> None: ...

    def lease_expired(self, new_status: DeliveryStatus) -> None: ...


class NoopHook:
    def attempt_recorded(
        self, outcome: AttemptOutcome, new_status: DeliveryStatus, duration_s: float
    ) -> None:
        pass

    def lease_expired(self, new_status: DeliveryStatus) -> None:
        pass
