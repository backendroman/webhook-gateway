class DomainError(Exception):
    code: str = "domain_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(DomainError):
    code = "not_found"


class UnsafeUrlError(DomainError):
    code = "unsafe_url"


class IdempotencyKeyReuseError(DomainError):
    code = "idempotency_key_reuse"


class ValidationError(DomainError):
    code = "validation_error"


class InvalidStateError(DomainError):
    code = "invalid_state"
