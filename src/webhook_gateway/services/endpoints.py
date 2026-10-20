import uuid
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from webhook_gateway.clock import Clock
from webhook_gateway.domain.enums import EndpointStatus
from webhook_gateway.domain.errors import NotFoundError
from webhook_gateway.domain.models import Endpoint
from webhook_gateway.domain.signing import generate_secret
from webhook_gateway.repositories.endpoints import EndpointRepository
from webhook_gateway.services.url_validator import UrlValidator

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class EndpointCreate:
    url: str
    description: str
    event_types: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EndpointPatch:
    url: str | None = None
    description: str | None = None
    event_types: tuple[str, ...] | None = None
    status: EndpointStatus | None = None


class EndpointService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        validator: UrlValidator,
        clock: Clock,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._validator = validator
        self._clock = clock

    async def create(self, data: EndpointCreate) -> Endpoint:
        await self._validator.validate(data.url)
        now = self._clock.now()
        endpoint = Endpoint(
            id=uuid.uuid4(),
            url=data.url,
            description=data.description,
            event_types=_dedupe(data.event_types),
            secret=generate_secret(),
            status=EndpointStatus.ACTIVE,
            paused_until=None,
            consecutive_failures=0,
            created_at=now,
            updated_at=now,
        )
        async with self._sessionmaker.begin() as session:
            await EndpointRepository(session).add(endpoint)
        log.info("endpoint_created", endpoint_id=str(endpoint.id))
        return endpoint

    async def get(self, endpoint_id: UUID) -> Endpoint:
        async with self._sessionmaker() as session:
            endpoint = await EndpointRepository(session).get(endpoint_id)
        if endpoint is None:
            raise NotFoundError("endpoint not found")
        return endpoint

    async def update(self, endpoint_id: UUID, patch: EndpointPatch) -> Endpoint:
        if patch.url is not None:
            await self._validator.validate(patch.url)
        values: dict[str, Any] = {}
        if patch.url is not None:
            values["url"] = patch.url
        if patch.description is not None:
            values["description"] = patch.description
        if patch.event_types is not None:
            values["event_types"] = list(_dedupe(patch.event_types))
        if patch.status is not None:
            # manual pause has no timer, resume resets the breaker counter
            values["status"] = patch.status.value
            values["paused_until"] = None
            if patch.status is EndpointStatus.ACTIVE:
                values["consecutive_failures"] = 0

        async with self._sessionmaker.begin() as session:
            repo = EndpointRepository(session)
            endpoint = (
                await repo.update(endpoint_id, self._clock.now(), **values)
                if values
                else await repo.get(endpoint_id)
            )
        if endpoint is None:
            raise NotFoundError("endpoint not found")
        log.info("endpoint_updated", endpoint_id=str(endpoint_id), fields=sorted(values))
        return endpoint

    async def delete(self, endpoint_id: UUID) -> None:
        async with self._sessionmaker.begin() as session:
            deleted = await EndpointRepository(session).delete(endpoint_id)
        if not deleted:
            raise NotFoundError("endpoint not found")
        log.info("endpoint_deleted", endpoint_id=str(endpoint_id))


def _dedupe(items: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(items))
