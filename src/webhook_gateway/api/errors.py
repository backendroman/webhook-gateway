from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from webhook_gateway.domain.errors import (
    DomainError,
    IdempotencyKeyReuseError,
    InvalidStateError,
    NotFoundError,
    UnsafeUrlError,
    ValidationError,
)

_STATUS: dict[type[DomainError], int] = {
    NotFoundError: status.HTTP_404_NOT_FOUND,
    UnsafeUrlError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    IdempotencyKeyReuseError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    InvalidStateError: status.HTTP_409_CONFLICT,
    ValidationError: status.HTTP_422_UNPROCESSABLE_CONTENT,
}


async def _handle_domain_error(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DomainError)  # noqa: S101
    code = _STATUS.get(type(exc), status.HTTP_400_BAD_REQUEST)
    return JSONResponse(status_code=code, content={"error": exc.code, "detail": exc.message})


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _handle_domain_error)
