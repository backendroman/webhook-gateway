import json

import pytest
import structlog

from webhook_gateway.logging import configure_logging


def test_secrets_are_redacted_in_json_logs(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", json=True)
    structlog.get_logger().info("x", secret="whsec_abc", signature="v1=1", endpoint_id="e1")
    line = json.loads(capsys.readouterr().out.strip())
    assert line["secret"] == "***"
    assert line["signature"] == "***"
    assert line["endpoint_id"] == "e1"
    assert "whsec_abc" not in json.dumps(line)
