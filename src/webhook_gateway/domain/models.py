from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from webhook_gateway.domain.enums import AttemptOutcome, DeliveryStatus, EndpointStatus


@dataclass(frozen=True, slots=True)
class Endpoint:
    id: UUID
    url: str
    description: str
    event_types: tuple[str, ...]
    secret: str = field(repr=False)  # keep it out of logs
    status: EndpointStatus
    paused_until: datetime | None
    consecutive_failures: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class Event:
    id: UUID
    type: str
    payload: Any
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Delivery:
    id: UUID
    event_id: UUID
    endpoint_id: UUID
    status: DeliveryStatus
    attempt_count: int
    next_attempt_at: datetime
    locked_until: datetime | None
    last_status_code: int | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ClaimedDelivery:
    id: UUID
    event_id: UUID
    endpoint_id: UUID
    attempt_number: int  # global number, already bumped by claim
    attempts_in_window: int  # since last replay, including this one
    lease_token: UUID
    url: str
    secret: str = field(repr=False)
    event_type: str = ""
    payload: Any = None
    event_created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Attempt:
    delivery_id: UUID
    attempt_number: int
    outcome: AttemptOutcome
    status_code: int | None
    response_body: str | None
    error: str | None
    duration_ms: int
    started_at: datetime
