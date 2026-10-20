from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from webhook_gateway.clock import Clock
from webhook_gateway.config import Settings
from webhook_gateway.metrics import WorkerMetrics
from webhook_gateway.services.url_validator import UrlValidator


@dataclass(frozen=True, slots=True)
class Container:
    settings: Settings
    clock: Clock
    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]
    url_validator: UrlValidator
    metrics: WorkerMetrics  # api uses only the queue gauges
