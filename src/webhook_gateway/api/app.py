from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from webhook_gateway.api.container import Container
from webhook_gateway.api.errors import register_error_handlers
from webhook_gateway.api.routers import deliveries, endpoints, events, health
from webhook_gateway.clock import Clock, SystemClock
from webhook_gateway.config import Settings
from webhook_gateway.db import create_engine, create_sessionmaker
from webhook_gateway.metrics import WorkerMetrics
from webhook_gateway.services.url_validator import Resolver, SystemResolver, UrlValidator


def create_app(
    settings: Settings,
    *,
    clock: Clock | None = None,
    resolver: Resolver | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        app.state.container = Container(
            settings=settings,
            clock=clock or SystemClock(),
            engine=engine,
            sessionmaker=create_sessionmaker(engine),
            url_validator=UrlValidator(resolver or SystemResolver(), settings.ssrf_allowlist),
            metrics=WorkerMetrics.create(),
        )
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(title="Webhook Delivery Gateway", version="0.1.0", lifespan=lifespan)
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(endpoints.router)
    app.include_router(events.router)
    app.include_router(deliveries.router)
    return app
