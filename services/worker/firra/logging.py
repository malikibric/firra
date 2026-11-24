"""Structured JSON logging for Cloud Logging.

Guardrail: logs carry ids, counts, timings, costs and error classes only. Free-text values are
redacted unless their key is on an explicit allowlist, so message bodies, quotes, email addresses
and tokens cannot leak through log fields by accident.
"""

import json
import logging
import sys
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any, Final
from uuid import UUID

# String fields that are safe identifiers or classifiers (never user content).
SAFE_STRING_KEYS: Final = frozenset(
    {
        "env",
        "error_class",
        "event",
        "method",
        "model",
        "path",
        "prompt_version",
        "provider",
        "provider_reason",
        "queue",
        "release",
        "route",
        "status",
        "task_type",
    }
)
REDACTED: Final = "[redacted]"
_MAX_SAFE_STRING = 200


def sanitize_value(key: str, value: object) -> object:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, str) and key in SAFE_STRING_KEYS:
        return value[:_MAX_SAFE_STRING]
    return REDACTED


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "severity": record.levelname,
            "logger": record.name,
            # The message is always a static event name (see Logger below), never content.
            "message": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            for key, value in fields.items():
                entry[str(key)] = sanitize_value(str(key), value)
        if record.exc_info and record.exc_info[0] is not None:
            # Exception class only; messages and tracebacks can contain content (Sentry has them).
            entry["error_class"] = record.exc_info[0].__name__
        return json.dumps(entry, separators=(",", ":"))


class Logger:
    """Thin wrapper that forces `event` names plus sanitized key/value fields."""

    def __init__(self, name: str) -> None:
        self._logger = logging.getLogger(name)

    def _log(self, level: int, event: str, exc: bool, fields: dict[str, object]) -> None:
        self._logger.log(level, event, extra={"fields": fields}, exc_info=exc)

    def debug(self, event: str, **fields: object) -> None:
        self._log(logging.DEBUG, event, False, fields)

    def info(self, event: str, **fields: object) -> None:
        self._log(logging.INFO, event, False, fields)

    def warning(self, event: str, **fields: object) -> None:
        self._log(logging.WARNING, event, False, fields)

    def error(self, event: str, *, exc: bool = False, **fields: object) -> None:
        self._log(logging.ERROR, event, exc, fields)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    # Access logs include full URLs and client addresses; request logging is done by Cloud Run.
    logging.getLogger("uvicorn.access").disabled = True
    # httpx/httpcore log every request URL at INFO; provider URLs can carry mailbox addresses.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str) -> Logger:
    return Logger(name)
