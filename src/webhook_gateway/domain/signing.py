# X-Webhook-Signature: v1=hex(hmac_sha256(secret, f"{timestamp}.{body}"))

import hashlib
import hmac
import secrets

SECRET_PREFIX = "whsec_"  # noqa: S105
SIGNATURE_VERSION = "v1"


def generate_secret() -> str:
    return SECRET_PREFIX + secrets.token_urlsafe(32)


def compute_signature(secret: str, timestamp: int, body: bytes) -> str:
    signed = str(timestamp).encode("ascii") + b"." + body
    digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"{SIGNATURE_VERSION}={digest}"


def verify_signature(
    secret: str,
    timestamp: int,
    body: bytes,
    signature: str,
    *,
    now: int,
    tolerance_s: int = 300,
) -> bool:
    if abs(now - timestamp) > tolerance_s:
        return False
    expected = compute_signature(secret, timestamp, body)
    return hmac.compare_digest(expected, signature)
