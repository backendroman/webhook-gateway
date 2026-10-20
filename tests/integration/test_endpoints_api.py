import uuid

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from webhook_gateway.repositories.tables import endpoints

pytestmark = pytest.mark.integration

NEW = {"url": "https://public.example/hook", "description": "d", "event_types": ["a", "b", "a"]}


async def _create(client: httpx.AsyncClient, **overrides: object) -> dict[str, object]:
    resp = await client.post("/v1/endpoints", json={**NEW, **overrides})
    assert resp.status_code == 201, resp.text
    data: dict[str, object] = resp.json()
    return data


async def test_create_returns_secret_once(client: httpx.AsyncClient) -> None:
    created = await _create(client)
    assert str(created["secret"]).startswith("whsec_")
    assert created["event_types"] == ["a", "b"]  # deduplicated, order preserved
    assert created["status"] == "active"

    fetched = (await client.get(f"/v1/endpoints/{created['id']}")).json()
    assert "secret" not in fetched
    assert fetched["url"] == NEW["url"]


async def test_secret_is_persisted(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    created = await _create(client)
    async with sessionmaker() as s:
        stored = await s.scalar(
            select(endpoints.c.secret).where(endpoints.c.id == uuid.UUID(str(created["id"])))
        )
    assert stored == created["secret"]


async def test_patch_updates_fields(client: httpx.AsyncClient) -> None:
    created = await _create(client)
    resp = await client.patch(
        f"/v1/endpoints/{created['id']}",
        json={"description": "new", "event_types": ["c"], "url": "https://dual.example/x"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["description"] == "new"
    assert body["event_types"] == ["c"]
    assert body["url"] == "https://dual.example/x"


async def test_patch_pause_and_resume(client: httpx.AsyncClient) -> None:
    created = await _create(client)
    paused = (
        await client.patch(f"/v1/endpoints/{created['id']}", json={"status": "paused"})
    ).json()
    assert paused["status"] == "paused"
    assert paused["paused_until"] is None
    resumed = (
        await client.patch(f"/v1/endpoints/{created['id']}", json={"status": "active"})
    ).json()
    assert resumed["status"] == "active"


async def test_empty_patch_is_noop(client: httpx.AsyncClient) -> None:
    created = await _create(client)
    resp = await client.patch(f"/v1/endpoints/{created['id']}", json={})
    assert resp.status_code == 200
    assert resp.json()["updated_at"] == created["updated_at"]


async def test_delete(client: httpx.AsyncClient) -> None:
    created = await _create(client)
    assert (await client.delete(f"/v1/endpoints/{created['id']}")).status_code == 204
    assert (await client.get(f"/v1/endpoints/{created['id']}")).status_code == 404
    assert (await client.delete(f"/v1/endpoints/{created['id']}")).status_code == 404


async def test_unknown_id_is_404(client: httpx.AsyncClient) -> None:
    missing = uuid.uuid4()
    assert (await client.get(f"/v1/endpoints/{missing}")).status_code == 404
    resp = await client.patch(f"/v1/endpoints/{missing}", json={"description": "x"})
    assert resp.status_code == 404


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.2/",  # 127.0.0.1 itself is allowlisted in test settings
        "http://[::1]/",
        "http://169.254.169.254/",
        "ftp://public.example/",
        "http://internal.example/",
    ],
)
async def test_create_rejects_unsafe_url(client: httpx.AsyncClient, url: str) -> None:
    resp = await client.post("/v1/endpoints", json={**NEW, "url": url})
    assert resp.status_code == 422
    assert resp.json()["error"] == "unsafe_url"


async def test_patch_rejects_unsafe_url(client: httpx.AsyncClient) -> None:
    created = await _create(client)
    resp = await client.patch(f"/v1/endpoints/{created['id']}", json={"url": "http://10.0.0.1/"})
    assert resp.status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"url": "https://public.example/", "event_types": []},
        {"url": "https://public.example/"},
        {"url": "https://public.example/", "event_types": [""]},
        {"url": "https://public.example/", "event_types": ["a"], "secret": "mine"},
    ],
)
async def test_create_validation_errors(client: httpx.AsyncClient, body: dict[str, object]) -> None:
    assert (await client.post("/v1/endpoints", json=body)).status_code == 422
