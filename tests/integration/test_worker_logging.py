import json
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes.clock import FakeClock
from tests.fakes.receiver import FakeReceiver, run_receiver
from tests.integration.worker_helpers import Harness
from webhook_gateway.logging import configure_logging

pytestmark = pytest.mark.integration


@pytest.fixture
async def receiver() -> AsyncIterator[FakeReceiver]:
    async with run_receiver() as r:
        yield r


async def test_attempt_log_has_ids_and_no_secret(
    sessionmaker: async_sessionmaker[AsyncSession],
    clock: FakeClock,
    receiver: FakeReceiver,
    capsys: pytest.CaptureFixture[str],
) -> None:
    receiver.always(500)
    async with httpx.AsyncClient() as http:
        h = Harness(sessionmaker, clock, http)
        endpoint_id = await h.endpoint(receiver.url)
        event_id = await h.event()
        secret = await h.secret_of(endpoint_id)
        configure_logging("INFO", json=True)
        capsys.readouterr()
        try:
            await h.worker().run_once()
        finally:
            configure_logging("INFO", json=False)
    out = capsys.readouterr().out

    assert secret not in out
    lines = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    [finished] = [entry for entry in lines if entry["event"] == "attempt_finished"]
    d = await h.delivery_of(event_id)
    assert finished["delivery_id"] == str(d.id)
    assert finished["event_id"] == str(event_id)
    assert finished["endpoint_id"] == str(endpoint_id)
    assert finished["attempt"] == 1
    assert finished["outcome"] == "retryable"
    assert finished["status_code"] == 500
    assert finished["level"] == "info"
