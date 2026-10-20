from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class QueueStats:
    due: int
    scheduled: int
    in_flight: int
    dead: int


_STATS_SQL = text(
    """
    SELECT
      (SELECT count(*) FROM deliveries
        WHERE status = 'pending' AND next_attempt_at <= :now) AS due,
      (SELECT count(*) FROM deliveries
        WHERE status = 'pending' AND next_attempt_at > :now) AS scheduled,
      (SELECT count(*) FROM deliveries WHERE status = 'in_flight') AS in_flight,
      (SELECT count(*) FROM deliveries WHERE status = 'dead') AS dead
    """
)


class StatsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def queue_stats(self, now: datetime) -> QueueStats:
        row = (await self._session.execute(_STATS_SQL, {"now": now})).one()
        return QueueStats(
            due=row.due, scheduled=row.scheduled, in_flight=row.in_flight, dead=row.dead
        )
