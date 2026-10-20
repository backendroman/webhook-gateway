from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints

from webhook_gateway.domain.models import Attempt, Delivery, Endpoint

EventType = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Url = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EndpointCreateIn(StrictModel):
    url: Url
    description: str = Field(default="", max_length=1000)
    event_types: list[EventType] = Field(min_length=1, max_length=100)


class EndpointPatchIn(StrictModel):
    url: Url | None = None
    description: str | None = Field(default=None, max_length=1000)
    event_types: list[EventType] | None = Field(default=None, min_length=1, max_length=100)
    status: Literal["active", "paused"] | None = None


class EventIn(StrictModel):
    type: EventType
    payload: JsonValue


class EventAcceptedOut(BaseModel):
    id: UUID
    type: str
    created_at: datetime
    deliveries: int


class DeliveryOut(BaseModel):
    id: UUID
    event_id: UUID
    endpoint_id: UUID
    status: str
    attempt_count: int
    next_attempt_at: datetime
    locked_until: datetime | None
    last_status_code: int | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, d: Delivery) -> "DeliveryOut":
        return cls(
            id=d.id,
            event_id=d.event_id,
            endpoint_id=d.endpoint_id,
            status=d.status.value,
            attempt_count=d.attempt_count,
            next_attempt_at=d.next_attempt_at,
            locked_until=d.locked_until,
            last_status_code=d.last_status_code,
            last_error=d.last_error,
            created_at=d.created_at,
            updated_at=d.updated_at,
        )


class AttemptOut(BaseModel):
    attempt_number: int
    outcome: str
    status_code: int | None
    response_body: str | None
    error: str | None
    duration_ms: int
    started_at: datetime

    @classmethod
    def from_domain(cls, a: Attempt) -> "AttemptOut":
        return cls(
            attempt_number=a.attempt_number,
            outcome=a.outcome.value,
            status_code=a.status_code,
            response_body=a.response_body,
            error=a.error,
            duration_ms=a.duration_ms,
            started_at=a.started_at,
        )


class DeliveryDetailsOut(DeliveryOut):
    attempts: list[AttemptOut]


class DeliveryPageOut(BaseModel):
    items: list[DeliveryOut]
    next_cursor: str | None


class EndpointOut(BaseModel):
    id: UUID
    url: str
    description: str
    event_types: list[str]
    status: str
    paused_until: datetime | None
    consecutive_failures: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, e: Endpoint) -> "EndpointOut":
        return cls(
            id=e.id,
            url=e.url,
            description=e.description,
            event_types=list(e.event_types),
            status=e.status.value,
            paused_until=e.paused_until,
            consecutive_failures=e.consecutive_failures,
            created_at=e.created_at,
            updated_at=e.updated_at,
        )


class EndpointCreatedOut(EndpointOut):
    secret: str  # returned only once, on create

    @classmethod
    def from_domain_with_secret(cls, e: Endpoint) -> "EndpointCreatedOut":
        return cls(**EndpointOut.from_domain(e).model_dump(), secret=e.secret)
