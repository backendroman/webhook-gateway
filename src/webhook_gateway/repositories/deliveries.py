import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, NamedTuple
from uuid import UUID

from sqlalchemy import Row, insert, select, text, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from webhook_gateway.domain.enums import DeliveryStatus
from webhook_gateway.domain.models import ClaimedDelivery, Delivery
from webhook_gateway.repositories.tables import deliveries


class RecoveredDelivery(NamedTuple):
    delivery_id: UUID
    attempt_number: int
    status: DeliveryStatus


@dataclass(frozen=True, slots=True)
class DeliveryFilter:
    status: DeliveryStatus | None = None
    endpoint_id: UUID | None = None
    event_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class PageCursor:
    created_at: datetime
    id: UUID


def _to_delivery(row: Row[Any]) -> Delivery:
    return Delivery(
        id=row.id,
        event_id=row.event_id,
        endpoint_id=row.endpoint_id,
        status=DeliveryStatus(row.status),
        attempt_count=row.attempt_count,
        next_attempt_at=row.next_attempt_at,
        locked_until=row.locked_until,
        last_status_code=row.last_status_code,
        last_error=row.last_error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class DeliveryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, delivery_id: UUID, *, for_update: bool = False) -> Delivery | None:
        stmt = select(deliveries).where(deliveries.c.id == delivery_id)
        if for_update:
            stmt = stmt.with_for_update()
        row = (await self._session.execute(stmt)).first()
        return _to_delivery(row) if row else None

    async def list_page(
        self, flt: DeliveryFilter, *, after: PageCursor | None, limit: int
    ) -> list[Delivery]:
        # newest first, (created_at, id) keyset
        stmt = select(deliveries)
        if flt.status is not None:
            stmt = stmt.where(deliveries.c.status == flt.status.value)
        if flt.endpoint_id is not None:
            stmt = stmt.where(deliveries.c.endpoint_id == flt.endpoint_id)
        if flt.event_id is not None:
            stmt = stmt.where(deliveries.c.event_id == flt.event_id)
        if after is not None:
            stmt = stmt.where(
                tuple_(deliveries.c.created_at, deliveries.c.id)
                < tuple_(after.created_at, after.id)
            )
        stmt = stmt.order_by(deliveries.c.created_at.desc(), deliveries.c.id.desc()).limit(limit)
        return [_to_delivery(r) for r in (await self._session.execute(stmt)).all()]

    async def requeue(self, delivery_id: UUID, now: datetime) -> Delivery | None:
        # keep attempt_count so attempt numbers stay unique,
        # moving attempt_window_start resets the budget
        stmt = (
            update(deliveries)
            .where(
                deliveries.c.id == delivery_id,
                deliveries.c.status.in_([DeliveryStatus.FAILED.value, DeliveryStatus.DEAD.value]),
            )
            .values(
                status=DeliveryStatus.PENDING.value,
                attempt_window_start=deliveries.c.attempt_count,
                next_attempt_at=now,
                last_error=None,
                last_status_code=None,
                updated_at=now,
            )
            .returning(*deliveries.c)
        )
        row = (await self._session.execute(stmt)).first()
        return _to_delivery(row) if row else None

    async def create_pending(
        self, event_id: UUID, endpoint_ids: Sequence[UUID], now: datetime
    ) -> int:
        if not endpoint_ids:
            return 0
        await self._session.execute(
            insert(deliveries),
            [
                {
                    "id": uuid.uuid4(),
                    "event_id": event_id,
                    "endpoint_id": endpoint_id,
                    "status": DeliveryStatus.PENDING.value,
                    "next_attempt_at": now,
                    "created_at": now,
                    "updated_at": now,
                }
                for endpoint_id in endpoint_ids
            ],
        )
        return len(endpoint_ids)

    async def claim_due(
        self, *, now: datetime, lease_until: datetime, limit: int
    ) -> list[ClaimedDelivery]:
        rows = (
            await self._session.execute(
                _CLAIM_SQL, {"now": now, "lease_until": lease_until, "limit": limit}
            )
        ).all()
        return [
            ClaimedDelivery(
                id=r.id,
                event_id=r.event_id,
                endpoint_id=r.endpoint_id,
                attempt_number=r.attempt_count,
                # this is what plan_next compares with max_attempts
                attempts_in_window=r.attempt_count - r.attempt_window_start,
                lease_token=r.lease_token,
                url=r.url,
                secret=r.secret,
                event_type=r.event_type,
                payload=r.payload,
                event_created_at=r.event_created_at,
            )
            for r in rows
        ]

    async def recover_expired_leases(
        self, *, now: datetime, max_attempts: int, limit: int
    ) -> list[RecoveredDelivery]:
        rows = (
            await self._session.execute(
                _RECOVER_SQL, {"now": now, "max_attempts": max_attempts, "limit": limit}
            )
        ).all()
        return [RecoveredDelivery(r.id, r.attempt_count, DeliveryStatus(r.status)) for r in rows]

    async def complete(
        self,
        *,
        delivery_id: UUID,
        lease_token: UUID,
        status: DeliveryStatus,
        next_attempt_at: datetime | None,
        last_status_code: int | None,
        last_error: str | None,
        now: datetime,
    ) -> bool:
        # False = lease already taken by someone else, result is dropped
        values: dict[str, Any] = {
            "status": status.value,
            "locked_until": None,
            "lease_token": None,
            "last_status_code": last_status_code,
            "last_error": last_error,
            "updated_at": now,
        }
        if next_attempt_at is not None:
            values["next_attempt_at"] = next_attempt_at
        stmt = (
            update(deliveries)
            .where(
                deliveries.c.id == delivery_id,
                deliveries.c.lease_token == lease_token,
                deliveries.c.status == DeliveryStatus.IN_FLIGHT.value,
            )
            .values(**values)
            .returning(deliveries.c.id)
        )
        return (await self._session.execute(stmt)).first() is not None


# attempt is counted at claim time, so a delivery that kills the worker still ends up dead
_CLAIM_SQL = text(
    """
    WITH due AS (
        SELECT d.id
        FROM deliveries AS d
        JOIN endpoints AS e ON e.id = d.endpoint_id
        WHERE d.status = 'pending'
          AND d.next_attempt_at <= :now
          AND (e.status = 'active' OR e.paused_until <= :now)
        ORDER BY d.next_attempt_at
        LIMIT :limit
        FOR UPDATE OF d SKIP LOCKED
    )
    UPDATE deliveries AS d
    SET status = 'in_flight',
        locked_until = :lease_until,
        lease_token = gen_random_uuid(),
        attempt_count = d.attempt_count + 1,
        updated_at = :now
    FROM due, endpoints AS e, events AS ev
    WHERE d.id = due.id AND e.id = d.endpoint_id AND ev.id = d.event_id
    RETURNING d.id, d.event_id, d.endpoint_id, d.attempt_count, d.attempt_window_start,
              d.lease_token, e.url, e.secret, ev.type AS event_type, ev.payload,
              ev.created_at AS event_created_at
    """
)

_RECOVER_SQL = text(
    """
    WITH expired AS (
        SELECT id
        FROM deliveries
        WHERE status = 'in_flight' AND locked_until <= :now
        ORDER BY locked_until
        LIMIT :limit
        FOR UPDATE SKIP LOCKED
    )
    UPDATE deliveries AS d
    SET status = CASE
            WHEN d.attempt_count - d.attempt_window_start >= :max_attempts THEN 'dead'
            ELSE 'pending'
        END,
        next_attempt_at = :now,
        locked_until = NULL,
        lease_token = NULL,
        last_status_code = NULL,
        last_error = 'lease expired: worker did not report a result',
        updated_at = :now
    FROM expired
    WHERE d.id = expired.id
    RETURNING d.id, d.attempt_count, d.status
    """
)
