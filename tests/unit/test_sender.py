from collections.abc import AsyncIterator, Callable

import httpx
import pytest

from tests.fakes.resolver import FakeResolver
from webhook_gateway.config import Settings
from webhook_gateway.domain.enums import AttemptOutcome
from webhook_gateway.services.url_validator import UrlValidator
from webhook_gateway.worker.sender import HttpSender, SendResult, create_http_client

URL = "https://public.example/hook"

Handler = Callable[[httpx.Request], httpx.Response]


# real servers stream the body; with content=... httpx reads it right away
class _Body(httpx.AsyncByteStream):
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self._data


def _resp(status: int, body: bytes = b"", headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, headers=headers, stream=_Body(body))


async def _send(handler: Handler, url: str = URL, max_bytes: int = 1024) -> SendResult:
    validator = UrlValidator(FakeResolver({"public.example": ["93.184.216.34"]}))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sender = HttpSender(
            client,
            validator,
            total_timeout_s=2.0,
            max_response_bytes=max_bytes,
            stored_response_bytes=10,
        )
        return await sender.send(url, {"X-Test": "1"}, b'{"a":1}')


async def test_success_passes_headers_and_body() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _resp(200, b"ok")

    result = await _send(handler)
    assert result == SendResult(AttemptOutcome.SUCCESS, status_code=200, response_body="ok")
    [req] = seen
    assert req.method == "POST"
    assert req.headers["x-test"] == "1"
    assert req.content == b'{"a":1}'


async def test_stored_body_is_cut() -> None:
    result = await _send(lambda _: _resp(500, b"y" * 100))
    assert result.outcome is AttemptOutcome.RETRYABLE
    assert result.response_body == "y" * 10


async def test_read_stops_at_max_response_bytes() -> None:
    result = await _send(lambda _: _resp(200, b"z" * 50_000), max_bytes=5)
    assert result.response_body == "zzzzz"


async def test_retry_after_header_is_returned() -> None:
    result = await _send(lambda _: _resp(429, headers={"Retry-After": "30"}))
    assert (result.outcome, result.retry_after) == (AttemptOutcome.RETRYABLE, "30")


async def test_4xx_is_permanent() -> None:
    result = await _send(lambda _: _resp(404))
    assert result.outcome is AttemptOutcome.PERMANENT


async def test_redirect_is_not_followed() -> None:
    result = await _send(lambda _: _resp(302, headers={"Location": "http://10.0.0.1/"}))
    assert (result.outcome, result.status_code) == (AttemptOutcome.RETRYABLE, 302)


async def test_blocked_url_is_not_sent() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("must not be called")

    result = await _send(handler, url="http://10.0.0.1/hook")
    assert result.outcome is AttemptOutcome.BLOCKED
    assert result.error


async def test_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    result = await _send(handler)
    assert result == SendResult(AttemptOutcome.TIMEOUT, error="timed out")


@pytest.mark.parametrize(
    "exc",
    [httpx.ConnectError("refused"), httpx.RemoteProtocolError("bad"), OSError("boom")],
)
async def test_network_errors(exc: Exception) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise exc

    result = await _send(handler)
    assert result.outcome is AttemptOutcome.NETWORK_ERROR
    assert result.error is not None
    assert result.error.startswith(type(exc).__name__)


async def test_error_text_is_limited() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("x" * 2000)

    result = await _send(handler)
    assert result.error is not None
    assert len(result.error) == 500


async def test_http_client_settings() -> None:
    settings = Settings(_env_file=None, http_connect_timeout_s=1.5, http_total_timeout_s=7)
    async with create_http_client(settings) as client:
        assert client.follow_redirects is False
        assert client.trust_env is False
        assert client.timeout.connect == 1.5
        assert client.timeout.read == 7
        assert client.headers["user-agent"].startswith("webhook-gateway/")
