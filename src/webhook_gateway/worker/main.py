import asyncio
import contextlib
import signal

import structlog
from prometheus_client import start_http_server

from webhook_gateway.clock import SystemClock
from webhook_gateway.config import Settings
from webhook_gateway.db import create_engine, create_sessionmaker
from webhook_gateway.logging import configure_logging
from webhook_gateway.metrics import MetricsHook, WorkerMetrics
from webhook_gateway.services.queue_stats import QueueGaugeUpdater
from webhook_gateway.services.url_validator import SystemResolver, UrlValidator
from webhook_gateway.worker.runner import DeliveryWorker, WorkerConfig
from webhook_gateway.worker.sender import HttpSender, create_http_client

log = structlog.get_logger(__name__)


def install_stop_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()

    def _request_stop(signame: str) -> None:
        if not stop.is_set():
            log.info("shutdown_requested", signal=signame)
            stop.set()

    signals = [signal.SIGTERM, signal.SIGINT]
    if hasattr(signal, "SIGBREAK"):  # windows
        signals.append(signal.SIGBREAK)
    for sig in signals:
        try:
            loop.add_signal_handler(sig, _request_stop, sig.name)
        except NotImplementedError:  # proactor loop on windows
            signal.signal(
                sig, lambda s, _f: loop.call_soon_threadsafe(_request_stop, signal.Signals(s).name)
            )


async def serve(settings: Settings) -> None:
    engine = create_engine(settings)
    sessionmaker = create_sessionmaker(engine)
    clock = SystemClock()
    validator = UrlValidator(SystemResolver(), settings.ssrf_allowlist)
    metrics = WorkerMetrics.create()
    http_server, _ = start_http_server(settings.worker_metrics_port, registry=metrics.registry)
    log.info("metrics_listening", port=settings.worker_metrics_port)
    stop = asyncio.Event()
    install_stop_handlers(stop)
    gauges = QueueGaugeUpdater(sessionmaker, metrics, clock)
    gauges_task = asyncio.create_task(gauges.run(stop, settings.worker_stats_interval_s))
    async with create_http_client(settings) as client:
        sender = HttpSender(
            client,
            validator,
            total_timeout_s=settings.http_total_timeout_s,
            max_response_bytes=settings.max_response_bytes,
            stored_response_bytes=settings.stored_response_bytes,
        )
        worker = DeliveryWorker(
            sessionmaker,
            sender,
            clock,
            WorkerConfig.from_settings(settings),
            hook=MetricsHook(metrics),
        )
        try:
            await worker.run(stop)
        finally:
            stop.set()
            await gauges_task
            http_server.shutdown()
            http_server.server_close()
            await engine.dispose()


def main() -> None:
    settings = Settings()
    configure_logging(settings.log_level, json=settings.log_json)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(serve(settings))


if __name__ == "__main__":
    main()
