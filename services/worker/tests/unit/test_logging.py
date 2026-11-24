import json
import logging
from uuid import uuid4

import pytest

from firra.logging import REDACTED, JsonFormatter, configure_logging, get_logger, sanitize_value


def test_free_text_is_redacted_unless_allowlisted() -> None:
    assert sanitize_value("subject", "Contract renewal for ACME") == REDACTED
    assert sanitize_value("email", "ceo@acme.com") == REDACTED
    assert sanitize_value("task_type", "diagnostics.ping") == "diagnostics.ping"


def test_ids_counts_and_timings_pass_through() -> None:
    job_id = uuid4()
    assert sanitize_value("job_id", job_id) == str(job_id)
    assert sanitize_value("count", 3) == 3
    assert sanitize_value("duration_ms", 12.5) == 12.5
    assert sanitize_value("nested", {"body": "x"}) == REDACTED


def test_json_output_has_cloud_logging_fields(caplog: pytest.LogCaptureFixture) -> None:
    caplog.handler.setFormatter(JsonFormatter())
    with caplog.at_level(logging.INFO):
        get_logger("t").info("task.succeeded", task_type="diagnostics.ping", body="secret text")
    entry = json.loads(caplog.handler.format(caplog.records[0]))
    assert entry["severity"] == "INFO"
    assert entry["message"] == "task.succeeded"
    assert entry["body"] == REDACTED
    assert "secret text" not in caplog.handler.format(caplog.records[0])


def test_exceptions_log_class_only(caplog: pytest.LogCaptureFixture) -> None:
    caplog.handler.setFormatter(JsonFormatter())
    try:
        raise ValueError("contains client@example.com")
    except ValueError:
        get_logger("t").error("task.failed", exc=True)
    output = caplog.handler.format(caplog.records[0])
    assert json.loads(output)["error_class"] == "ValueError"
    assert "client@example.com" not in output


def test_http_client_request_urls_are_not_logged() -> None:
    root = logging.getLogger()
    handlers, level = root.handlers, root.level
    try:
        configure_logging("INFO")
        assert not logging.getLogger("httpx").isEnabledFor(logging.INFO)
        assert not logging.getLogger("httpcore").isEnabledFor(logging.INFO)
    finally:
        root.handlers, root.level = handlers, level
