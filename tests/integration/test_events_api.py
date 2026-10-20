import asyncio
import uuid

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from webhook_gateway.repositories.tables import deliveries, events, idempotency_keys

pytestmark = pytest.mark.integration

EVENT = {"type": "order.created", "payload": {"order_id": 42, "items": [1, 2]}}


async def _endpoint(client: httpx.AsyncClient, types: list[str]) -> str:
    resp = await client.post(
        "/v1/endpoints", json={"url": "https://public.example/h", "event_types": types}
    )
    assert resp.status_code == 201
    return str(resp.json()["id"])


async def _count(sm: async_sessionmaker[AsyncSession], table: object) -> int:
    async with sm() as s:
        return int(await s.scalar(select(func.count()).select_from(table)) or 0)  # type: ignore[arg-type]


async def test_accepts_event_and_fans_out_to_subscribers(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    a = await _endpoint(client, ["order.created", "order.paid"])
    b = await _endpoint(client, ["order.created"])
    await _endpoint(client, ["user.created"])

    resp = await client.post("/v1/events", json=EVENT, headers={"Idempotency-Key": "k1"})
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["deliveries"] == 2
    assert "Idempotent-Replayed" not in resp.headers

    # visible from another session -> committed
    async with sessionmaker() as s:
        rows = (
            await s.execute(
                select(deliveries.c.endpoint_id, deliveries.c.status).where(
                    deliveries.c.event_id == uuid.UUID(body["id"])
                )
            )
        ).all()
    assert {str(r.endpoint_id) for r in rows} == {a, b}
    assert {r.status for r in rows} == {"pending"}


async def test_event_without_subscribers_is_stored(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    resp = await client.post("/v1/events", json=EVENT, headers={"Idempotency-Key": "k-none"})
    assert resp.status_code == 202
    assert resp.json()["deliveries"] == 0
    assert await _count(sessionmaker, events) == 1


async def test_repeat_with_same_body_returns_same_response_without_duplicates(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await _endpoint(client, ["order.created"])
    first = await client.post("/v1/events", json=EVENT, headers={"Idempotency-Key": "k2"})
    raw = '{"payload": {"items": [1, 2], "order_id": 42},   "type": "order.created"}'
    second = await client.post(
        "/v1/events",
        content=raw,
        headers={"Idempotency-Key": "k2", "Content-Type": "application/json"},
    )
    assert second.status_code == 202
    assert second.json() == first.json()
    assert second.headers["Idempotent-Replayed"] == "true"
    assert await _count(sessionmaker, events) == 1
    assert await _count(sessionmaker, deliveries) == 1


async def test_same_key_different_body_is_rejected(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await client.post("/v1/events", json=EVENT, headers={"Idempotency-Key": "k3"})
    other = {**EVENT, "payload": {"order_id": 43}}
    resp = await client.post("/v1/events", json=other, headers={"Idempotency-Key": "k3"})
    assert resp.status_code == 422
    assert resp.json()["error"] == "idempotency_key_reuse"
    assert await _count(sessionmaker, events) == 1


async def test_concurrent_requests_with_same_key_create_one_event(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await _endpoint(client, ["order.created"])
    responses = await asyncio.gather(
        *(
            client.post("/v1/events", json=EVENT, headers={"Idempotency-Key": "race"})
            for _ in range(10)
        )
    )
    assert {r.status_code for r in responses} == {202}
    assert len({r.json()["id"] for r in responses}) == 1
    assert sum(r.headers.get("Idempotent-Replayed") == "true" for r in responses) == 9
    assert await _count(sessionmaker, events) == 1
    assert await _count(sessionmaker, deliveries) == 1
    assert await _count(sessionmaker, idempotency_keys) == 1


async def test_different_keys_create_distinct_events(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    for key in ("a", "b"):
        assert (
            await client.post("/v1/events", json=EVENT, headers={"Idempotency-Key": key})
        ).status_code == 202
    assert await _count(sessionmaker, events) == 2


async def test_idempotency_key_header_is_required(client: httpx.AsyncClient) -> None:
    assert (await client.post("/v1/events", json=EVENT)).status_code == 422
    too_long = {"Idempotency-Key": "x" * 256}
    assert (await client.post("/v1/events", json=EVENT, headers=too_long)).status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"payload": {}},
        {"type": "", "payload": {}},
        {"type": "t"},
        {"type": "t", "payload": {}, "extra": 1},
    ],
)
async def test_body_validation(client: httpx.AsyncClient, body: dict[str, object]) -> None:
    resp = await client.post("/v1/events", json=body, headers={"Idempotency-Key": "v"})
    assert resp.status_code == 422


async def test_payload_may_be_any_json(client: httpx.AsyncClient) -> None:
    for i, payload in enumerate([[1, 2], "s", 1.5, None, {"nested": {"a": [True]}}]):
        resp = await client.post(
            "/v1/events",
            json={"type": "t", "payload": payload},
            headers={"Idempotency-Key": f"p{i}"},
        )
        assert resp.status_code == 202, resp.text
