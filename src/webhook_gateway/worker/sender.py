import asyncio
from dataclasses import dataclass
from typing import Protocol

import httpx

from webhook_gateway.config import Settings
from webhook_gateway.domain.classification import classify_status
from webhook_gateway.domain.enums import AttemptOutcome
from webhook_gateway.domain.errors import UnsafeUrlError
from webhook_gateway.services.url_validator import UrlValidator


@dataclass(frozen=True, slots=True)
class SendResult:
    outcome: AttemptOutcome
    status_code: int | None = None
    response_body: str | None = None
    error: str | None = None
    retry_after: str | None = None


class Sender(Protocol):
    async def send(self, url: str, headers: dict[str, str], body: bytes) -> SendResult: ...


def create_http_client(settings: Settings) -> httpx.AsyncClient:
    timeout = httpx.Timeout(
        connect=settings.http_connect_timeout_s,
        read=settings.http_total_timeout_s,
        write=settings.http_total_timeout_s,
        pool=settings.http_connect_timeout_s,
    )
    return httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,  # redirect may lead to an internal address
        limits=httpx.Limits(max_connections=settings.worker_concurrency * 2),
        trust_env=False,  # ignore HTTP_PROXY etc
        headers={"User-Agent": "webhook-gateway/0.1"},
    )


class HttpSender:
    def __init__(
        self,
        client: httpx.AsyncClient,
        validator: UrlValidator,
        *,
        total_timeout_s: float,
        max_response_bytes: int,
        stored_response_bytes: int,
    ) -> None:
        self._client = client
        self._validator = validator
        self._total_timeout_s = total_timeout_s
        self._max_response_bytes = max_response_bytes
        self._stored_response_bytes = stored_response_bytes

    async def send(self, url: str, headers: dict[str, str], body: bytes) -> SendResult:
        # check again, DNS could change since the endpoint was created
        try:
            await self._validator.validate(url)
        except UnsafeUrlError as exc:
            return SendResult(AttemptOutcome.BLOCKED, error=exc.message)

        try:
            async with asyncio.timeout(self._total_timeout_s):
                return await self._post(url, headers, body)
        except (TimeoutError, httpx.TimeoutException):
            return SendResult(AttemptOutcome.TIMEOUT, error="timed out")
        except (httpx.HTTPError, OSError) as exc:
            return SendResult(AttemptOutcome.NETWORK_ERROR, error=_describe(exc))

    async def _post(self, url: str, headers: dict[str, str], body: bytes) -> SendResult:
        async with self._client.stream("POST", url, headers=headers, content=body) as resp:
            raw = await self._read_limited(resp)
            return SendResult(
                outcome=classify_status(resp.status_code),
                status_code=resp.status_code,
                response_body=raw[: self._stored_response_bytes].decode("utf-8", "replace"),
                retry_after=resp.headers.get("Retry-After"),
            )

    async def _read_limited(self, resp: httpx.Response) -> bytes:
        buf = bytearray()
        async for chunk in resp.aiter_raw():
            buf.extend(chunk)
            if len(buf) >= self._max_response_bytes:
                break
        return bytes(buf[: self._max_response_bytes])


def _describe(exc: BaseException) -> str:
    text = str(exc) or type(exc).__name__
    return f"{type(exc).__name__}: {text}"[:500]
