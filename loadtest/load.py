"""Ingest N events via API, then deliver them with W workers. All in one process

WARNING: truncates all tables.
    WG_DATABASE_URL=postgresql+asyncpg://... uv run python loadtest/load.py --events 5000
"""

import argparse
import asyncio
import contextlib
import socket
import statistics
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import uvicorn
from sqlalchemy import text
from starlette.types import Receive, Scope, Send

from webhook_gateway.api.app import create_app
from webhook_gateway.clock import SystemClock
from webhook_gateway.config import Settings
from webhook_gateway.db import create_engine, create_sessionmaker
from webhook_gateway.logging import configure_logging
from webhook_gateway.services.url_validator import SystemResolver, UrlValidator
from webhook_gateway.worker.runner import DeliveryWorker, WorkerConfig
from webhook_gateway.worker.sender import HttpSender, create_http_client


class CountingReceiver:
    def __init__(self) -> None:
        self.count = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        while (await receive()).get("more_body"):
            pass
        self.count += 1
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


@contextlib.asynccontextmanager
async def serve(app: Any) -> AsyncIterator[str]:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1024)
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
    server.capture_signals = contextlib.nullcontext  # type: ignore[method-assign,assignment]
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        server.should_exit = True
        await task


def pct(values: list[float], p: int) -> float:
    return statistics.quantiles(values, n=100)[p - 1]


async def count_succeeded(sm: Any) -> int:
    async with sm() as s:
        return int(await s.scalar(text("SELECT count(*) FROM deliveries WHERE status='succeeded'")))


async def main(args: argparse.Namespace) -> None:
    settings = Settings(
        ssrf_allowlist=["127.0.0.1"],
        log_level="WARNING",
        worker_batch_size=args.batch,
        worker_concurrency=args.concurrency,
        worker_poll_interval_s=0.2,
        db_pool_size=args.workers * 2 + args.ingest_concurrency,
    )
    configure_logging("WARNING")
    engine = create_engine(settings)
    sm = create_sessionmaker(engine)
    async with engine.begin() as conn:
        await conn.execute(
            text("TRUNCATE attempts, deliveries, idempotency_keys, events, endpoints CASCADE")
        )

    receiver = CountingReceiver()
    async with (
        serve(receiver) as receiver_url,
        serve(create_app(settings)) as api_url,
        httpx.AsyncClient(base_url=api_url, timeout=30) as api,
        create_http_client(settings) as out_client,
    ):
        await asyncio.sleep(0.5)
        for i in range(args.endpoints):
            r = await api.post(
                "/v1/endpoints", json={"url": f"{receiver_url}/{i}", "event_types": ["load"]}
            )
            r.raise_for_status()

        # 1) ingest only, no workers running
        latencies: list[float] = []
        sem = asyncio.Semaphore(args.ingest_concurrency)
        pad = "x" * args.payload_bytes

        async def post_one(i: int) -> None:
            async with sem:
                t = time.perf_counter()
                r = await api.post(
                    "/v1/events",
                    json={"type": "load", "payload": {"n": i, "pad": pad}},
                    headers={"Idempotency-Key": str(uuid.uuid4())},
                )
                latencies.append(time.perf_counter() - t)
                r.raise_for_status()

        t0 = time.perf_counter()
        await asyncio.gather(*(post_one(i) for i in range(args.events)))
        ingest_s = time.perf_counter() - t0
        expected = args.events * args.endpoints

        # 2) delivery
        sender = HttpSender(
            out_client,
            UrlValidator(SystemResolver(), settings.ssrf_allowlist),
            total_timeout_s=settings.http_total_timeout_s,
            max_response_bytes=settings.max_response_bytes,
            stored_response_bytes=settings.stored_response_bytes,
        )
        cfg = WorkerConfig.from_settings(settings)
        workers = [DeliveryWorker(sm, sender, SystemClock(), cfg) for _ in range(args.workers)]
        stop = asyncio.Event()
        t1 = time.perf_counter()
        tasks = [asyncio.create_task(w.run(stop)) for w in workers]
        while await count_succeeded(sm) < expected:  # noqa: ASYNC110
            await asyncio.sleep(0.1)
        deliver_s = time.perf_counter() - t1
        stop.set()
        await asyncio.gather(*tasks)

        async with sm() as s:
            extra = await s.scalar(text("SELECT count(*) FROM attempts WHERE attempt_number > 1"))

    await engine.dispose()
    ms = [x * 1000 for x in latencies]
    print(f"events={args.events} endpoints={args.endpoints} deliveries={expected}")
    print(
        f"ingest:  {args.events / ingest_s:.0f} events/s ({ingest_s:.2f}s, "
        f"concurrency {args.ingest_concurrency}); latency ms "
        f"p50={pct(ms, 50):.1f} p95={pct(ms, 95):.1f} p99={pct(ms, 99):.1f}"
    )
    print(
        f"deliver: {expected / deliver_s:.0f} deliveries/s ({deliver_s:.2f}s, "
        f"{args.workers} workers x concurrency {args.concurrency}, batch {args.batch})"
    )
    print(f"receiver requests={receiver.count} retried_attempts={extra}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--events", type=int, default=2000)
    p.add_argument("--endpoints", type=int, default=2)
    p.add_argument("--ingest-concurrency", type=int, default=50)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--concurrency", type=int, default=20)
    p.add_argument("--batch", type=int, default=50)
    p.add_argument("--payload-bytes", type=int, default=256)
    asyncio.run(main(p.parse_args()))
