from datetime import UTC, datetime, timedelta

from webhook_gateway.clock import SystemClock


def test_system_clock_is_utc() -> None:
    now = SystemClock().now()
    assert now.tzinfo is UTC
    assert abs(datetime.now(UTC) - now) < timedelta(seconds=5)
