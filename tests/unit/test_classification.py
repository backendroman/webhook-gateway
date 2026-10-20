import pytest

from webhook_gateway.domain.classification import classify_status, honors_retry_after
from webhook_gateway.domain.enums import AttemptOutcome


@pytest.mark.parametrize("code", [200, 201, 202, 204, 299])
def test_2xx_is_success(code: int) -> None:
    assert classify_status(code) is AttemptOutcome.SUCCESS


@pytest.mark.parametrize("code", [400, 401, 403, 404, 405, 409, 410, 413, 422, 451, 499])
def test_4xx_is_permanent(code: int) -> None:
    assert classify_status(code) is AttemptOutcome.PERMANENT


@pytest.mark.parametrize("code", [408, 429])
def test_408_and_429_are_retryable(code: int) -> None:
    assert classify_status(code) is AttemptOutcome.RETRYABLE


@pytest.mark.parametrize("code", [500, 502, 503, 504, 599, 301, 302, 307, 101, 199, 600])
def test_5xx_3xx_and_others_are_retryable(code: int) -> None:
    assert classify_status(code) is AttemptOutcome.RETRYABLE


def test_retry_after_only_for_429_and_503() -> None:
    assert honors_retry_after(429)
    assert honors_retry_after(503)
    assert not honors_retry_after(500)
    assert not honors_retry_after(None)
