from typing import Any
from uuid import UUID

from sqlalchemy import Row, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from webhook_gateway.domain.enums import AttemptOutcome
from webhook_gateway.domain.models import Attempt
from webhook_gateway.repositories.tables import attempts


def _to_attempt(row: Row[Any]) -> Attempt:
    return Attempt(
        delivery_id=row.delivery_id,
        attempt_number=row.attempt_number,
        outcome=AttemptOutcome(row.outcome),
        status_code=row.status_code,
        response_body=row.response_body,
        error=row.error,
        duration_ms=row.duration_ms,
        started_at=row.started_at,
    )


class AttemptRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_many(self, items: list[Attempt]) -> None:
        if not items:
            return
        # unique (delivery_id, attempt_number): second write of the same attempt is a no-op
        stmt = insert(attempts).on_conflict_do_nothing(
            index_elements=[attempts.c.delivery_id, attempts.c.attempt_number]
        )
        await self._session.execute(
            stmt,
            [
                {
                    "delivery_id": a.delivery_id,
                    "attempt_number": a.attempt_number,
                    "outcome": a.outcome.value,
                    "status_code": a.status_code,
                    "response_body": a.response_body,
                    "error": a.error,
                    "duration_ms": a.duration_ms,
                    "started_at": a.started_at,
                }
                for a in items
            ],
        )

    async def list_for_delivery(self, delivery_id: UUID) -> list[Attempt]:
        stmt = (
            select(attempts)
            .where(attempts.c.delivery_id == delivery_id)
            .order_by(attempts.c.attempt_number)
        )
        return [_to_attempt(r) for r in (await self._session.execute(stmt)).all()]
