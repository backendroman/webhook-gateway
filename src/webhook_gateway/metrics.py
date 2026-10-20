from dataclasses import dataclass

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

from webhook_gateway.domain.enums import AttemptOutcome, DeliveryStatus

LATENCY_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)


@dataclass(frozen=True, slots=True)
class WorkerMetrics:
    registry: CollectorRegistry
    attempts: Counter
    attempt_latency: Histogram
    transitions: Counter
    queue_depth: Gauge
    dead_total: Gauge

    @classmethod
    def create(cls, registry: CollectorRegistry | None = None) -> "WorkerMetrics":
        reg = registry or CollectorRegistry()
        return cls(
            registry=reg,
            attempts=Counter(
                "webhook_delivery_attempts_total",
                "Delivery attempts by outcome",
                ["outcome"],
                registry=reg,
            ),
            attempt_latency=Histogram(
                "webhook_delivery_attempt_duration_seconds",
                "Wall time of one HTTP delivery attempt",
                ["outcome"],
                buckets=LATENCY_BUCKETS,
                registry=reg,
            ),
            transitions=Counter(
                "webhook_delivery_final_status_total",
                "Deliveries reaching a terminal status (succeeded, failed, dead)",
                ["status"],
                registry=reg,
            ),
            queue_depth=Gauge(
                "webhook_queue_depth",
                "Deliveries waiting in the queue (pending), split by due or scheduled",
                ["state"],
                registry=reg,
            ),
            dead_total=Gauge(
                "webhook_dead_deliveries",
                "Deliveries currently in dead-letter status",
                registry=reg,
            ),
        )


_TERMINAL = frozenset({DeliveryStatus.SUCCEEDED, DeliveryStatus.FAILED, DeliveryStatus.DEAD})


class MetricsHook:
    def __init__(self, metrics: WorkerMetrics) -> None:
        self._m = metrics

    def attempt_recorded(
        self, outcome: AttemptOutcome, new_status: DeliveryStatus, duration_s: float
    ) -> None:
        self._m.attempts.labels(outcome=outcome.value).inc()
        self._m.attempt_latency.labels(outcome=outcome.value).observe(duration_s)
        if new_status in _TERMINAL:
            self._m.transitions.labels(status=new_status.value).inc()

    def lease_expired(self, new_status: DeliveryStatus) -> None:
        self._m.attempts.labels(outcome=AttemptOutcome.LEASE_EXPIRED.value).inc()
        if new_status is DeliveryStatus.DEAD:
            self._m.transitions.labels(status=new_status.value).inc()
