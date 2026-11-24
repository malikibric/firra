"""Sentry error reporting (EU region DSN). Disabled when no DSN is configured."""

from typing import Any

import sentry_sdk

from firra.settings import Settings

_SAFE_HEADERS = frozenset({"content-type", "user-agent", "x-cloudtasks-queuename"})


def scrub_event(event: Any, _hint: Any) -> Any:
    request = event.get("request")
    if isinstance(request, dict):
        request.pop("data", None)
        request.pop("cookies", None)
        request.pop("query_string", None)
        headers = request.get("headers")
        if isinstance(headers, dict):
            request["headers"] = {k: v for k, v in headers.items() if k.lower() in _SAFE_HEADERS}
    event.pop("user", None)
    return event


def init_sentry(settings: Settings) -> None:
    if settings.sentry_dsn is None:
        return
    sentry_sdk.init(
        dsn=settings.sentry_dsn.get_secret_value(),
        environment=settings.env,
        release=settings.release,
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        traces_sample_rate=0.0,
        before_send=scrub_event,
    )
