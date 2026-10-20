import pytest

from webhook_gateway.logging import configure_logging


@pytest.fixture(autouse=True)
def _logging() -> None:
    # structlog keeps sys.stdout from configure() and capsys closes it later
    configure_logging("INFO", json=False)
