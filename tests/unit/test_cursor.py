import uuid
from datetime import UTC, datetime

import pytest

from webhook_gateway.domain.errors import ValidationError
from webhook_gateway.repositories.deliveries import PageCursor
from webhook_gateway.services.deliveries import decode_cursor, encode_cursor


def test_cursor_roundtrip() -> None:
    c = PageCursor(created_at=datetime(2026, 1, 2, 3, 4, 5, 678901, tzinfo=UTC), id=uuid.uuid4())
    assert decode_cursor(encode_cursor(c)) == c


@pytest.mark.parametrize(
    "bad", ["", "!!!", "e30", "eyJ0IjoxfQ", "eyJ0IjoiMjAyNi0wMS0wMSIsImkiOiJ4In0"]
)
def test_invalid_cursor_rejected(bad: str) -> None:
    with pytest.raises(ValidationError):
        decode_cursor(bad)
