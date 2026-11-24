"""Local/test Vertex tokens; KMS, GCS and queues keep metadata tokens.

Docs checked 2026-09-30: https://docs.cloud.google.com/sdk/gcloud/reference/auth/print-access-token
The fixed command uses the active account and normally returns a one-hour access token.
"""

import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field


class GcloudTokenError(Exception):
    """Token acquisition failed; command output must never reach callers or logs."""


def _run(argv: list[str]) -> str:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        argv, capture_output=True, text=True, check=True, timeout=30
    ).stdout


@dataclass
class GcloudTokenSource:
    runner: Callable[[list[str]], str] = _run
    clock: Callable[[], float] = time.monotonic
    _token: str = field(default="", init=False, repr=False)
    _expires_at: float = field(default=0, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def token(self) -> str:
        with self._lock:
            if self._token and self.clock() < self._expires_at:
                return self._token
            try:
                token = self.runner(["gcloud", "auth", "print-access-token"]).strip()
            except (OSError, subprocess.SubprocessError):
                raise GcloudTokenError("gcloud token acquisition failed") from None
            if not token or any(char.isspace() for char in token):
                raise GcloudTokenError("gcloud returned no usable token")
            self._token = token
            self._expires_at = self.clock() + 45 * 60
            return token
