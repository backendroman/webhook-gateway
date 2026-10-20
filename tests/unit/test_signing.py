import hashlib
import hmac

from webhook_gateway.domain.signing import (
    SECRET_PREFIX,
    compute_signature,
    generate_secret,
    verify_signature,
)

SECRET = "whsec_test"
BODY = b'{"id":"1","type":"order.created"}'
TS = 1_700_000_000


def test_signature_matches_reference_hmac() -> None:
    expected = hmac.new(SECRET.encode(), f"{TS}.".encode() + BODY, hashlib.sha256).hexdigest()
    assert compute_signature(SECRET, TS, BODY) == f"v1={expected}"


def test_signature_is_deterministic_and_input_sensitive() -> None:
    sig = compute_signature(SECRET, TS, BODY)
    assert sig == compute_signature(SECRET, TS, BODY)
    assert sig != compute_signature(SECRET, TS + 1, BODY)
    assert sig != compute_signature(SECRET, TS, BODY + b" ")
    assert sig != compute_signature("whsec_other", TS, BODY)


def test_verify_accepts_valid_signature_within_tolerance() -> None:
    sig = compute_signature(SECRET, TS, BODY)
    assert verify_signature(SECRET, TS, BODY, sig, now=TS + 299)


def test_verify_rejects_tampering_and_stale_timestamp() -> None:
    sig = compute_signature(SECRET, TS, BODY)
    assert not verify_signature(SECRET, TS, BODY + b"x", sig, now=TS)
    assert not verify_signature("whsec_wrong", TS, BODY, sig, now=TS)
    assert not verify_signature(SECRET, TS, BODY, sig, now=TS + 301)
    assert not verify_signature(SECRET, TS, BODY, "v1=deadbeef", now=TS)


def test_generated_secrets_are_prefixed_and_unique() -> None:
    secrets = {generate_secret() for _ in range(100)}
    assert len(secrets) == 100
    assert all(s.startswith(SECRET_PREFIX) and len(s) > 40 for s in secrets)
