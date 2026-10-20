import random
import uuid
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes.clock import FakeClock
from tests.fakes.resolver import FakeResolver
from webhook_gateway.domain.circuit_breaker import BreakerPolicy
from webhook_gateway.domain.models import Attempt
from webhook_gateway.domain.retry import RetryPolicy
from webhook_gateway.repositories.attempts import AttemptRepository
from webhook_gateway.repositories.endpoints import EndpointRepository
from webhook_gateway.repositories.tables import deliveries
from webhook_gateway.services.endpoints import EndpointCreate, EndpointService
from webhook_gateway.services.events import EventService
from webhook_gateway.services.url_validator import UrlValidator
from webhook_gateway.worker.runner import DeliveryWorker, WorkerConfig
from webhook_gateway.worker.sender import HttpSender

ALLOW_LOCAL = UrlValidator(FakeResolver(), allowlist=["127.0.0.1"])


def worker_config(**overrides: Any) -> WorkerConfig:
    base: dict[str, Any] = {
        "batch_size": 10,
        "concurrency": 5,
        "poll_interval_s": 0.05,
        "lease_s": 30.0,
        "shutdown_grace_s": 5.0,
        "retry": RetryPolicy(max_attempts=3, base_s=10.0, cap_s=100.0),
        "breaker": BreakerPolicy(threshold=1000, cooldown_s=60.0),  # off
    }
    base.update(overrides)
    return WorkerConfig(**base)


def make_sender(client: httpx.AsyncClient, total_timeout_s: float = 2.0) -> HttpSender:
    return HttpSender(
        client,
        ALLOW_LOCAL,
        total_timeout_s=total_timeout_s,
        max_response_bytes=1024,
        stored_response_bytes=64,
    )


@dataclass
class Harness:
    sm: async_sessionmaker[AsyncSession]
    clock: FakeClock
    client: httpx.AsyncClient

    def worker(self, *, total_timeout_s: float = 2.0, **cfg: Any) -> DeliveryWorker:
        return DeliveryWorker(
            self.sm,
            make_sender(self.client, total_timeout_s),
            self.clock,
            worker_config(**cfg),
            rng=random.Random(0),
        )

    async def endpoint(self, url: str, types: tuple[str, ...] = ("t",)) -> UUID:
        svc = EndpointService(self.sm, ALLOW_LOCAL, self.clock)
        return (await svc.create(EndpointCreate(url=url, description="", event_types=types))).id

    async def event(self, payload: Any = None, event_type: str = "t") -> UUID:
        res = await EventService(self.sm, self.clock).ingest(
            str(uuid.uuid4()), event_type, payload if payload is not None else {"n": 1}
        )
        return UUID(res.body["id"])

    async def delivery_of(self, event_id: UUID) -> Any:
        async with self.sm() as s:
            return (
                await s.execute(select(deliveries).where(deliveries.c.event_id == event_id))
            ).one()

    async def all_deliveries(self) -> list[Any]:
        async with self.sm() as s:
            return list((await s.execute(select(deliveries))).all())

    async def secret_of(self, endpoint_id: UUID) -> str:
        async with self.sm() as s:
            endpoint = await EndpointRepository(s).get(endpoint_id)
        assert endpoint is not None
        return endpoint.secret

    async def attempts(self, delivery_id: UUID) -> list[Attempt]:
        async with self.sm() as s:
            return await AttemptRepository(s).list_for_delivery(delivery_id)
