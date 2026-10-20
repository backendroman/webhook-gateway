from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from webhook_gateway.repositories.tables import idempotency_keys


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    key: str
    request_hash: bytes
    event_id: UUID
    response_status: int
    response_body: dict[str, Any]


class IdempotencyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def try_insert(self, record: IdempotencyRecord, now: datetime) -> bool:
        # a parallel insert of the same key waits here until the other tx finishes
        stmt = (
            insert(idempotency_keys)
            .values(
                key=record.key,
                request_hash=record.request_hash,
                event_id=record.event_id,
                response_status=record.response_status,
                response_body=record.response_body,
                created_at=now,
            )
            .on_conflict_do_nothing(index_elements=[idempotency_keys.c.key])
            .returning(idempotency_keys.c.key)
        )
        return (await self._session.execute(stmt)).first() is not None

    async def get(self, key: str) -> IdempotencyRecord | None:
        row = (
            await self._session.execute(
                select(idempotency_keys).where(idempotency_keys.c.key == key)
            )
        ).first()
        if row is None:
            return None
        return IdempotencyRecord(
            key=row.key,
            request_hash=bytes(row.request_hash),
            event_id=row.event_id,
            response_status=row.response_status,
            response_body=row.response_body,
        )
