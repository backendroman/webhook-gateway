import base64
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from webhook_gateway.clock import Clock
from webhook_gateway.domain.enums import DeliveryStatus
from webhook_gateway.domain.errors import InvalidStateError, NotFoundError, ValidationError
from webhook_gateway.domain.models import Attempt, Delivery
from webhook_gateway.repositories.attempts import AttemptRepository
from webhook_gateway.repositories.deliveries import (
    DeliveryFilter,
    DeliveryRepository,
    PageCursor,
)

log = structlog.get_logger(__name__)

REPLAYABLE = frozenset({DeliveryStatus.FAILED, DeliveryStatus.DEAD})


@dataclass(frozen=True, slots=True)
class DeliveryPage:
    items: list[Delivery]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class DeliveryDetails:
    delivery: Delivery
    attempts: list[Attempt]


def encode_cursor(c: PageCursor) -> str:
    raw = json.dumps({"t": c.created_at.isoformat(), "i": str(c.id)}).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(value: str) -> PageCursor:
    try:
        padded = value + "=" * (-len(value) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded))
        created_at = datetime.fromisoformat(data["t"])
        if created_at.tzinfo is None:
            raise ValueError("naive timestamp")
        return PageCursor(created_at=created_at, id=UUID(data["i"]))
    except (ValueError, KeyError, TypeError) as exc:
        raise ValidationError("invalid cursor") from exc


class DeliveryService:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], clock: Clock) -> None:
        self._sessionmaker = sessionmaker
        self._clock = clock

    async def list(self, flt: DeliveryFilter, *, cursor: str | None, limit: int) -> DeliveryPage:
        after = decode_cursor(cursor) if cursor else None
        async with self._sessionmaker() as session:
            # +1 row to know if there is a next page
            rows = await DeliveryRepository(session).list_page(flt, after=after, limit=limit + 1)
        items = rows[:limit]
        next_cursor = None
        if len(rows) > limit:
            last = items[-1]
            next_cursor = encode_cursor(PageCursor(created_at=last.created_at, id=last.id))
        return DeliveryPage(items=items, next_cursor=next_cursor)

    async def get(self, delivery_id: UUID) -> DeliveryDetails:
        async with self._sessionmaker() as session:
            delivery = await DeliveryRepository(session).get(delivery_id)
            if delivery is None:
                raise NotFoundError("delivery not found")
            attempts = await AttemptRepository(session).list_for_delivery(delivery_id)
        return DeliveryDetails(delivery=delivery, attempts=attempts)

    async def replay(self, delivery_id: UUID) -> Delivery:
        now = self._clock.now()
        async with self._sessionmaker.begin() as session:
            repo = DeliveryRepository(session)
            requeued = await repo.requeue(delivery_id, now)
            if requeued is None:
                current = await repo.get(delivery_id)
                if current is None:
                    raise NotFoundError("delivery not found")
                raise InvalidStateError(
                    f"only failed or dead deliveries can be replayed (status: {current.status})"
                )
        log.info("delivery_replayed", delivery_id=str(delivery_id))
        return requeued
