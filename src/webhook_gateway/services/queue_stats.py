import asyncio
import contextlib

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from webhook_gateway.clock import Clock
from webhook_gateway.metrics import WorkerMetrics
from webhook_gateway.repositories.stats import StatsRepository

log = structlog.get_logger(__name__)


class QueueGaugeUpdater:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        metrics: WorkerMetrics,
        clock: Clock,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._metrics = metrics
        self._clock = clock

    async def refresh(self) -> None:
        async with self._sessionmaker() as session:
            stats = await StatsRepository(session).queue_stats(self._clock.now())
        self._metrics.queue_depth.labels(state="due").set(stats.due)
        self._metrics.queue_depth.labels(state="scheduled").set(stats.scheduled)
        self._metrics.queue_depth.labels(state="in_flight").set(stats.in_flight)
        self._metrics.dead_total.set(stats.dead)

    async def run(self, stop: asyncio.Event, interval_s: float) -> None:
        while not stop.is_set():
            try:
                await self.refresh()
            except Exception:
                log.exception("queue_stats_failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=interval_s)
