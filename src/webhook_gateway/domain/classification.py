from webhook_gateway.domain.enums import AttemptOutcome

RETRYABLE_4XX = frozenset({408, 429})
RETRY_AFTER_STATUSES = frozenset({429, 503})


def classify_status(status_code: int) -> AttemptOutcome:
    if 200 <= status_code < 300:
        return AttemptOutcome.SUCCESS
    if 400 <= status_code < 500 and status_code not in RETRYABLE_4XX:
        return AttemptOutcome.PERMANENT
    # 5xx, 3xx (we don't follow redirects) and anything weird
    return AttemptOutcome.RETRYABLE


def honors_retry_after(status_code: int | None) -> bool:
    return status_code in RETRY_AFTER_STATUSES
