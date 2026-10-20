import pytest
from pydantic import ValidationError

from webhook_gateway.config import Settings


def test_allowlist_parsed_from_csv_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WG_SSRF_ALLOWLIST", "localhost, 10.0.0.0/8 ,")
    assert Settings().ssrf_allowlist == ["localhost", "10.0.0.0/8"]


def test_lease_must_outlive_request_timeout() -> None:
    with pytest.raises(ValidationError, match="worker_lease_s"):
        Settings(_env_file=None, worker_lease_s=5, http_total_timeout_s=10)


def test_defaults_are_sane() -> None:
    s = Settings()
    assert s.http_connect_timeout_s <= s.http_total_timeout_s
    assert s.ssrf_allowlist == []


def test_connect_timeout_cannot_exceed_total() -> None:
    with pytest.raises(ValidationError, match="http_connect_timeout_s"):
        Settings(_env_file=None, http_connect_timeout_s=20, http_total_timeout_s=10)


def test_allowlist_as_list() -> None:
    assert Settings(_env_file=None, ssrf_allowlist=["a", "b"]).ssrf_allowlist == ["a", "b"]
