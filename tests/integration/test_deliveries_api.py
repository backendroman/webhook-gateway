from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes.clock import FakeClock
from tests.fakes.receiver import FakeReceiver, run_receiver
from tests.integration.worker_helpers import Harness

pytestmark = pytest.mark.integration


@pytest.fixture
async def receiver() -> AsyncIterator[FakeReceiver]:
    async with run_receiver() as r:
        yield r


@pytest.fixture
async def h(
    sessionmaker: async_sessionmaker[AsyncSession], clock: FakeClock
) -> AsyncIterator[Harness]:
    async with httpx.AsyncClient(timeout=5.0) as http:
        yield Harness(sessionmaker, clock, http)


async def test_retries_reach_dead_then_replay_delivers(
    h: Harness, client: httpx.AsyncClient, receiver: FakeReceiver
) -> None:
    receiver.always(500)
    await h.endpoint(receiver.url)
    event_id = await h.event()
    worker = h.worker()  # max_attempts=3
    for _ in range(3):
        await worker.run_once()
        h.clock.advance(1000)

    d = await h.delivery_of(event_id)
    assert d.status == "dead"

    detail = (await client.get(f"/v1/deliveries/{d.id}")).json()
    assert detail["status"] == "dead"
    assert [a["attempt_number"] for a in detail["attempts"]] == [1, 2, 3]
    assert {a["status_code"] for a in detail["attempts"]} == {500}

    resp = await client.post(f"/v1/deliveries/{d.id}/replay")
    assert resp.status_code == 202
    assert resp.json()["status"] == "pending"

    receiver.always(200)
    assert await worker.run_once() == 1
    detail = (await client.get(f"/v1/deliveries/{d.id}")).json()
    assert detail["status"] == "succeeded"
    assert detail["attempt_count"] == 4
    assert [a["outcome"] for a in detail["attempts"]] == ["retryable"] * 3 + ["success"]


async def test_replay_gives_full_retry_budget(
    h: Harness, client: httpx.AsyncClient, receiver: FakeReceiver
) -> None:
    receiver.always(500)
    await h.endpoint(receiver.url)
    event_id = await h.event()
    worker = h.worker()
    for _ in range(3):
        await worker.run_once()
        h.clock.advance(1000)
    d = await h.delivery_of(event_id)
    await client.post(f"/v1/deliveries/{d.id}/replay")

    for expected in ("pending", "pending", "dead"):
        await worker.run_once()
        h.clock.advance(1000)
        assert (await h.delivery_of(event_id)).status == expected
    assert (await h.delivery_of(event_id)).attempt_count == 6


async def test_replay_failed_4xx(
    h: Harness, client: httpx.AsyncClient, receiver: FakeReceiver
) -> None:
    receiver.always(400)
    await h.endpoint(receiver.url)
    event_id = await h.event()
    await h.worker().run_once()
    d = await h.delivery_of(event_id)
    assert d.status == "failed"
    assert (await client.post(f"/v1/deliveries/{d.id}/replay")).status_code == 202


async def test_replay_rejects_non_terminal_states(
    h: Harness, client: httpx.AsyncClient, receiver: FakeReceiver
) -> None:
    receiver.always(200)
    await h.endpoint(receiver.url)
    event_id = await h.event()
    d = await h.delivery_of(event_id)
    resp = await client.post(f"/v1/deliveries/{d.id}/replay")  # pending
    assert resp.status_code == 409
    assert resp.json()["error"] == "invalid_state"

    await h.worker().run_once()
    assert (await client.post(f"/v1/deliveries/{d.id}/replay")).status_code == 409  # succeeded
    missing = "00000000-0000-0000-0000-000000000000"
    assert (await client.post(f"/v1/deliveries/{missing}/replay")).status_code == 404
    assert (await client.get(f"/v1/deliveries/{missing}")).status_code == 404


async def test_list_keyset_pagination_and_filters(h: Harness, client: httpx.AsyncClient) -> None:
    a = await h.endpoint("http://127.0.0.1:9/a")
    b = await h.endpoint("http://127.0.0.1:9/b")
    event_ids = []
    for i in range(7):
        event_ids.append(await h.event({"i": i}))
        h.clock.advance(1)  # distinct created_at
    # same created_at, order by id
    for i in range(3):
        event_ids.append(await h.event({"j": i}))

    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        params: dict[str, str | int] = {"limit": 4}
        if cursor:
            params["cursor"] = cursor
        page = (await client.get("/v1/deliveries", params=params)).json()
        seen += [d["id"] for d in page["items"]]
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == 20  # 10 events x 2 endpoints
    assert len(set(seen)) == 20  # no duplicates across pages
    assert pages == 5

    full = (await client.get("/v1/deliveries", params={"limit": 200})).json()["items"]
    keys = [(d["created_at"], d["id"]) for d in full]
    assert keys == sorted(keys, reverse=True)
    assert [d["id"] for d in full] == seen

    by_ep = (await client.get("/v1/deliveries", params={"endpoint_id": str(a)})).json()["items"]
    assert {d["endpoint_id"] for d in by_ep} == {str(a)}
    assert len(by_ep) == 10
    by_ev = (await client.get("/v1/deliveries", params={"event_id": str(event_ids[0])})).json()
    assert {d["endpoint_id"] for d in by_ev["items"]} == {str(a), str(b)}
    by_status = (await client.get("/v1/deliveries", params={"status": "dead"})).json()
    assert by_status["items"] == []


async def test_list_validation(client: httpx.AsyncClient) -> None:
    assert (await client.get("/v1/deliveries", params={"status": "nope"})).status_code == 422
    assert (await client.get("/v1/deliveries", params={"limit": 0})).status_code == 422
    assert (await client.get("/v1/deliveries", params={"cursor": "garbage"})).status_code == 422
