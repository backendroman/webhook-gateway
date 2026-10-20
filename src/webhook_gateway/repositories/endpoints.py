from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Row, delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from webhook_gateway.domain.circuit_breaker import BreakerState
from webhook_gateway.domain.enums import EndpointStatus
from webhook_gateway.domain.models import Endpoint
from webhook_gateway.repositories.tables import endpoints


def _to_endpoint(row: Row[Any]) -> Endpoint:
    return Endpoint(
        id=row.id,
        url=row.url,
        description=row.description,
        event_types=tuple(row.event_types),
        secret=row.secret,
        status=EndpointStatus(row.status),
        paused_until=row.paused_until,
        consecutive_failures=row.consecutive_failures,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class EndpointRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, endpoint: Endpoint) -> None:
        await self._session.execute(
            insert(endpoints).values(
                id=endpoint.id,
                url=endpoint.url,
                description=endpoint.description,
                event_types=list(endpoint.event_types),
                secret=endpoint.secret,
                status=endpoint.status.value,
                paused_until=endpoint.paused_until,
                consecutive_failures=endpoint.consecutive_failures,
                created_at=endpoint.created_at,
                updated_at=endpoint.updated_at,
            )
        )

    async def get(self, endpoint_id: UUID, *, for_update: bool = False) -> Endpoint | None:
        stmt = select(endpoints).where(endpoints.c.id == endpoint_id)
        if for_update:
            stmt = stmt.with_for_update()
        row = (await self._session.execute(stmt)).first()
        return _to_endpoint(row) if row else None

    async def update(self, endpoint_id: UUID, now: datetime, **values: Any) -> Endpoint | None:
        stmt = (
            update(endpoints)
            .where(endpoints.c.id == endpoint_id)
            .values(**values, updated_at=now)
            .returning(*endpoints.c)
        )
        row = (await self._session.execute(stmt)).first()
        return _to_endpoint(row) if row else None

    async def delete(self, endpoint_id: UUID) -> bool:
        result = await self._session.execute(
            delete(endpoints).where(endpoints.c.id == endpoint_id).returning(endpoints.c.id)
        )
        return result.first() is not None

    async def lock_breaker(self, endpoint_id: UUID) -> BreakerState | None:
        # FOR UPDATE so parallel workers don't lose consecutive_failures increments
        stmt = (
            select(endpoints.c.status, endpoints.c.paused_until, endpoints.c.consecutive_failures)
            .where(endpoints.c.id == endpoint_id)
            .with_for_update()
        )
        row = (await self._session.execute(stmt)).first()
        if row is None:
            return None
        return BreakerState(
            status=EndpointStatus(row.status),
            paused_until=row.paused_until,
            consecutive_failures=row.consecutive_failures,
        )

    async def save_breaker(self, endpoint_id: UUID, state: BreakerState, now: datetime) -> None:
        await self._session.execute(
            update(endpoints)
            .where(endpoints.c.id == endpoint_id)
            .values(
                status=state.status.value,
                paused_until=state.paused_until,
                consecutive_failures=state.consecutive_failures,
                updated_at=now,
            )
        )

    async def ids_subscribed_to(self, event_type: str) -> list[UUID]:
        # paused too, deliveries just wait in the queue
        stmt = select(endpoints.c.id).where(endpoints.c.event_types.contains([event_type]))
        return list((await self._session.execute(stmt)).scalars())
