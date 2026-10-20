import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from webhook_gateway.clock import Clock
from webhook_gateway.domain.errors import IdempotencyKeyReuseError
from webhook_gateway.domain.models import Event
from webhook_gateway.repositories.deliveries import DeliveryRepository
from webhook_gateway.repositories.endpoints import EndpointRepository
from webhook_gateway.repositories.events import EventRepository
from webhook_gateway.repositories.idempotency import IdempotencyRecord, IdempotencyRepository

log = structlog.get_logger(__name__)

ACCEPTED = 202


@dataclass(frozen=True, slots=True)
class IngestResult:
    status_code: int
    body: dict[str, Any]
    replayed: bool


def request_fingerprint(event_type: str, payload: Any) -> bytes:
    # canonical json: key order and whitespace don't matter
    canonical = json.dumps(
        {"type": event_type, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).digest()


class EventService:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], clock: Clock) -> None:
        self._sessionmaker = sessionmaker
        self._clock = clock

    async def ingest(self, idempotency_key: str, event_type: str, payload: Any) -> IngestResult:
        fingerprint = request_fingerprint(event_type, payload)
        now = self._clock.now()
        event = Event(id=uuid.uuid4(), type=event_type, payload=payload, created_at=now)

        # event + key + deliveries in one tx, 202 only after commit
        async with self._sessionmaker() as session:
            await EventRepository(session).add(event)
            endpoint_ids = await EndpointRepository(session).ids_subscribed_to(event_type)
            body = {
                "id": str(event.id),
                "type": event.type,
                "created_at": now.isoformat(),
                "deliveries": len(endpoint_ids),
            }
            record = IdempotencyRecord(
                key=idempotency_key,
                request_hash=fingerprint,
                event_id=event.id,
                response_status=ACCEPTED,
                response_body=body,
            )
            # False -> key already exists, the stored response is returned below
            inserted = await IdempotencyRepository(session).try_insert(record, now)
            if inserted:
                await DeliveryRepository(session).create_pending(event.id, endpoint_ids, now)
                await session.commit()
            else:
                await session.rollback()

        if inserted:
            log.info("event_accepted", event_id=str(event.id), deliveries=len(endpoint_ids))
            return IngestResult(status_code=ACCEPTED, body=body, replayed=False)
        return await self._replay(idempotency_key, fingerprint)

    async def _replay(self, key: str, fingerprint: bytes) -> IngestResult:
        async with self._sessionmaker() as session:
            existing = await IdempotencyRepository(session).get(key)
        if existing is None:  # pragma: no cover
            raise IdempotencyKeyReuseError("idempotency key state changed, retry the request")
        if existing.request_hash != fingerprint:
            raise IdempotencyKeyReuseError(
                "Idempotency-Key was already used with a different request body"
            )
        log.info("event_replayed", event_id=str(existing.event_id))
        return IngestResult(
            status_code=existing.response_status, body=existing.response_body, replayed=True
        )
