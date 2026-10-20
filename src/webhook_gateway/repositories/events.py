from uuid import UUID

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from webhook_gateway.domain.models import Event
from webhook_gateway.repositories.tables import events


class EventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, event: Event) -> None:
        await self._session.execute(
            insert(events).values(
                id=event.id, type=event.type, payload=event.payload, created_at=event.created_at
            )
        )

    async def get(self, event_id: UUID) -> Event | None:
        row = (await self._session.execute(select(events).where(events.c.id == event_id))).first()
        if row is None:
            return None
        return Event(id=row.id, type=row.type, payload=row.payload, created_at=row.created_at)
