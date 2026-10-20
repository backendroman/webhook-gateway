from enum import StrEnum


class EndpointStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    IN_FLIGHT = "in_flight"
    SUCCEEDED = "succeeded"
    FAILED = "failed"  # 4xx, no retries
    DEAD = "dead"  # retries exhausted


class AttemptOutcome(StrEnum):
    SUCCESS = "success"
    RETRYABLE = "retryable"
    PERMANENT = "permanent"
    TIMEOUT = "timeout"
    NETWORK_ERROR = "network_error"
    BLOCKED = "blocked"  # SSRF check failed right before sending
    LEASE_EXPIRED = "lease_expired"  # worker died, result unknown
