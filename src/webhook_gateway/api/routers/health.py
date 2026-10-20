from fastapi import APIRouter, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from webhook_gateway.api.deps import ContainerDep
from webhook_gateway.services.queue_stats import QueueGaugeUpdater

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(container: ContainerDep, response: Response) -> dict[str, str]:
    try:
        async with container.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable", "database": "down"}
    return {"status": "ok", "database": "up"}


@router.get("/metrics", include_in_schema=False)
async def metrics(container: ContainerDep) -> Response:
    # queue gauges from the db only, attempt counters live on each worker's :9100
    m = container.metrics
    await QueueGaugeUpdater(container.sessionmaker, m, container.clock).refresh()
    return Response(generate_latest(m.registry), media_type=CONTENT_TYPE_LATEST)
