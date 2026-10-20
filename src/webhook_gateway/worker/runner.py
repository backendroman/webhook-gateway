"""recover expired leases -> claim -> send -> record

Claim is committed before the HTTP call, the result goes in a separate tx checked by
lease_token. If the worker dies in between, any other worker puts the row back
"""

import asyncio
import json
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from webhook_gateway.clock import Clock
from webhook_gateway.config import Settings
from webhook_gateway.domain.circuit_breaker import BreakerPolicy, BreakerState, on_attempt
from webhook_gateway.domain.enums import AttemptOutcome, EndpointStatus
from webhook_gateway.domain.models import Attempt, ClaimedDelivery
from webhook_gateway.domain.retry import RetryPolicy, parse_retry_after, plan_next
from webhook_gateway.domain.signing import compute_signature
from webhook_gateway.repositories.attempts import AttemptRepository
from webhook_gateway.repositories.deliveries import DeliveryRepository
from webhook_gateway.repositories.endpoints import EndpointRepository
from webhook_gateway.worker.hooks import AttemptHook, NoopHook
from webhook_gateway.worker.sender import Sender, SendResult

log = structlog.get_logger(__name__)
type Logger = structlog.typing.FilteringBoundLogger

LEASE_EXPIRED_ERROR = "lease expired: worker did not report a result"


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    batch_size: int
    concurrency: int
    poll_interval_s: float
    lease_s: float
    shutdown_grace_s: float
    retry: RetryPolicy
    breaker: BreakerPolicy

    @classmethod
    def from_settings(cls, s: Settings) -> "WorkerConfig":
        return cls(
            batch_size=s.worker_batch_size,
            concurrency=s.worker_concurrency,
            poll_interval_s=s.worker_poll_interval_s,
            lease_s=s.worker_lease_s,
            shutdown_grace_s=s.worker_shutdown_grace_s,
            retry=RetryPolicy(
                max_attempts=s.delivery_max_attempts,
                base_s=s.backoff_base_s,
                cap_s=s.backoff_max_s,
            ),
            breaker=BreakerPolicy(
                threshold=s.breaker_failure_threshold, cooldown_s=s.breaker_cooldown_s
            ),
        )


def build_body(c: ClaimedDelivery) -> bytes:
    envelope = {
        "id": str(c.event_id),
        "type": c.event_type,
        "created_at": c.event_created_at.isoformat() if c.event_created_at else None,
        "data": c.payload,
    }
    return json.dumps(envelope, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def build_headers(c: ClaimedDelivery, body: bytes, now: datetime) -> dict[str, str]:
    timestamp = int(now.timestamp())
    return {
        "Content-Type": "application/json",
        "X-Webhook-Id": str(c.event_id),
        "X-Webhook-Timestamp": str(timestamp),
        "X-Webhook-Signature": compute_signature(c.secret, timestamp, body),
    }


class DeliveryWorker:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        sender: Sender,
        clock: Clock,
        config: WorkerConfig,
        *,
        rng: random.Random | None = None,
        hook: AttemptHook | None = None,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._sender = sender
        self._clock = clock
        self._config = config
        self._rng = rng or random.Random()  # noqa: S311
        self._hook: AttemptHook = hook or NoopHook()

    async def run_once(self) -> int:
        # one batch, used by tests
        await self.recover_expired_leases()
        claimed = await self.claim(self._config.batch_size)
        sem = asyncio.Semaphore(self._config.concurrency)

        async def bounded(c: ClaimedDelivery) -> None:
            async with sem:
                await self.process(c)

        await asyncio.gather(*(bounded(c) for c in claimed))
        return len(claimed)

    async def run(self, stop: asyncio.Event) -> None:
        log.info("worker_started", concurrency=self._config.concurrency)
        tasks: set[asyncio.Task[None]] = set()
        # _done sets it, so a freed slot doesn't wait for poll_interval
        slot_freed = asyncio.Event()

        def _done(task: asyncio.Task[None]) -> None:
            tasks.discard(task)
            slot_freed.set()

        while not stop.is_set():
            claimed: list[ClaimedDelivery] = []
            free = self._config.concurrency - len(tasks)
            try:
                await self.recover_expired_leases()
                if free > 0:
                    claimed = await self.claim(min(free, self._config.batch_size))
            except Exception:
                log.exception("worker_poll_failed")
            for c in claimed:
                task = asyncio.create_task(self.process(c))
                tasks.add(task)
                task.add_done_callback(_done)
            if claimed and len(tasks) < self._config.concurrency:
                continue  # got something, probably there is more
            slot_freed.clear()
            await _wait_any(stop, slot_freed, max_wait_s=self._config.poll_interval_s)

        await self._drain(tasks)
        log.info("worker_stopped")

    async def _drain(self, tasks: set[asyncio.Task[None]]) -> None:
        if not tasks:
            return
        log.info("worker_draining", in_flight=len(tasks))
        _, pending = await asyncio.wait(set(tasks), timeout=self._config.shutdown_grace_s)
        for task in pending:
            task.cancel()  # lease expires, someone will retry it
        if pending:
            log.warning("worker_drain_timeout", abandoned=len(pending))
            await asyncio.gather(*pending, return_exceptions=True)

    async def recover_expired_leases(self) -> int:
        now = self._clock.now()
        async with self._sessionmaker.begin() as session:
            recovered = await DeliveryRepository(session).recover_expired_leases(
                now=now, max_attempts=self._config.retry.max_attempts, limit=1000
            )
            await AttemptRepository(session).add_many(
                [
                    Attempt(
                        delivery_id=r.delivery_id,
                        attempt_number=r.attempt_number,
                        outcome=AttemptOutcome.LEASE_EXPIRED,
                        status_code=None,
                        response_body=None,
                        error=LEASE_EXPIRED_ERROR,
                        duration_ms=0,
                        started_at=now,
                    )
                    for r in recovered
                ]
            )
        for r in recovered:
            self._hook.lease_expired(r.status)
            log.warning(
                "lease_expired",
                delivery_id=str(r.delivery_id),
                attempt=r.attempt_number,
                new_status=r.status.value,
            )
        return len(recovered)

    async def claim(self, limit: int) -> list[ClaimedDelivery]:
        now = self._clock.now()
        lease_until = now + timedelta(seconds=self._config.lease_s)
        async with self._sessionmaker.begin() as session:
            return await DeliveryRepository(session).claim_due(
                now=now, lease_until=lease_until, limit=limit
            )

    async def process(self, c: ClaimedDelivery) -> None:
        bound: Logger = log.bind(
            delivery_id=str(c.id),
            event_id=str(c.event_id),
            endpoint_id=str(c.endpoint_id),
            attempt=c.attempt_number,
        )
        try:
            body = build_body(c)
            started_at = self._clock.now()
            t0 = time.perf_counter()
            result = await self._sender.send(c.url, build_headers(c, body, started_at), body)
            duration_s = time.perf_counter() - t0
            await self._record(c, result, started_at=started_at, duration_s=duration_s, log=bound)
        except Exception:
            # nothing recorded, lease expires and it gets picked up again
            bound.exception("attempt_processing_failed")

    async def _record(
        self,
        c: ClaimedDelivery,
        result: SendResult,
        *,
        started_at: datetime,
        duration_s: float,
        log: Logger,
    ) -> None:
        now = self._clock.now()
        step = plan_next(
            outcome=result.outcome,
            attempts_used=c.attempts_in_window,
            status_code=result.status_code,
            retry_after_s=parse_retry_after(result.retry_after, now),
            now=now,
            policy=self._config.retry,
            rng=self._rng,
        )
        duration_ms = max(0, round(duration_s * 1000))
        breaker_before: BreakerState | None = None
        # None: result dropped or endpoint deleted
        breaker_after: BreakerState | None = None
        async with self._sessionmaker.begin() as session:
            # endpoint first, then delivery: same order as ON DELETE CASCADE, no deadlock
            endpoints_repo = EndpointRepository(session)
            breaker_before = await endpoints_repo.lock_breaker(c.endpoint_id)
            recorded = await DeliveryRepository(session).complete(
                delivery_id=c.id,
                lease_token=c.lease_token,
                status=step.status,
                next_attempt_at=step.next_attempt_at,
                last_status_code=result.status_code,
                last_error=result.error,
                now=now,
            )
            if recorded:
                await AttemptRepository(session).add_many(
                    [
                        Attempt(
                            delivery_id=c.id,
                            attempt_number=c.attempt_number,
                            outcome=result.outcome,
                            status_code=result.status_code,
                            response_body=result.response_body,
                            error=result.error,
                            duration_ms=duration_ms,
                            started_at=started_at,
                        )
                    ]
                )
                if breaker_before is not None:
                    breaker_after = on_attempt(
                        breaker_before,
                        success=result.outcome is AttemptOutcome.SUCCESS,
                        now=now,
                        policy=self._config.breaker,
                    )
                    if breaker_after != breaker_before:
                        await endpoints_repo.save_breaker(c.endpoint_id, breaker_after, now)
        if not recorded:
            # our lease expired and another worker took it
            log.warning("lease_lost_result_discarded", outcome=result.outcome.value)
            return
        self._hook.attempt_recorded(result.outcome, step.status, duration_s)
        if breaker_before is not None and breaker_after is not None:
            _log_breaker_transition(log, breaker_before, breaker_after)
        log.info(
            "attempt_finished",
            outcome=result.outcome.value,
            status_code=result.status_code,
            duration_ms=duration_ms,
            new_status=step.status.value,
            next_attempt_at=step.next_attempt_at.isoformat() if step.next_attempt_at else None,
        )


def _log_breaker_transition(log: Logger, before: BreakerState, after: BreakerState) -> None:
    opened = after.paused_until is not None and after.paused_until != before.paused_until
    closed = before.status is EndpointStatus.PAUSED and after.status is EndpointStatus.ACTIVE
    if opened:
        log.warning(
            "endpoint_paused_by_breaker",
            paused_until=after.paused_until.isoformat() if after.paused_until else None,
            consecutive_failures=after.consecutive_failures,
        )
    elif closed:
        log.info("endpoint_resumed_by_breaker")


async def _wait_any(*events: asyncio.Event, max_wait_s: float) -> None:
    waiters = [asyncio.create_task(e.wait()) for e in events]
    try:
        await asyncio.wait(waiters, timeout=max_wait_s, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for w in waiters:
            w.cancel()
        await asyncio.gather(*waiters, return_exceptions=True)
