"""Common CLI boundary. Conversation packets are pulled via MCP (L3-04), so
``conversation`` deliberately has no synchronous ``run`` implementation here.

Bind an executor to the local runtime's pool; cloud pools remain unbound.
Concrete CLI executors plug into this protocol in L3-02/03.
"""

import json
import re
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol
from weakref import WeakKeyDictionary

from psycopg import Connection
from psycopg.rows import DictRow
from psycopg_pool import ConnectionPool

from firra.resources import local_schemas_dir


def extraction_schema(path: Path | None = None) -> dict[str, Any]:
    """The checked-in OpenAI-strict contract; callers may supply its bundled location."""
    source = path or local_schemas_dir() / "ai_extraction_output.json"
    schema: dict[str, Any] = json.loads(source.read_text())
    return {key: value for key, value in schema.items() if key not in ("$schema", "$id")}


class ExecutorKind(StrEnum):
    claude_code = "claude_code"
    codex = "codex"
    conversation = "conversation"


@dataclass(frozen=True, slots=True)
class ExecutorResult:
    raw: str
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


class ExecutorError(Exception):
    def __init__(self) -> None:
        super().__init__(type(self).__name__)

    @property
    def error_class(self) -> str:
        """Database/app error classes use snake_case, as the existing task errors do."""
        return re.sub(r"(?<!^)(?=[A-Z])", "_", type(self).__name__).lower()


class ExecutorNotInstalled(ExecutorError):  # noqa: N818 - contract error class
    pass


class ExecutorNotSignedIn(ExecutorError):  # noqa: N818 - contract error class
    pass


class ExecutorLimitReached(ExecutorError):  # noqa: N818 - contract error class
    def __init__(self, resume_at: datetime | None = None) -> None:
        super().__init__()
        self.resume_at = resume_at


class ExecutorTimeout(ExecutorError):  # noqa: N818 - contract error class
    pass


class ExecutorFailed(ExecutorError):  # noqa: N818 - contract error class
    pass


class ExecutorMalformed(ExecutorError):  # noqa: N818 - contract error class
    pass


class ExtractionExecutor(Protocol):
    @property
    def kind(self) -> ExecutorKind: ...

    def run(
        self, prompt: str, packet_text: str, schema: dict[str, Any], *, timeout_seconds: float
    ) -> ExecutorResult: ...


class FakeExecutor:
    """Offline scripted answers/errors; call text stays in test memory only."""

    def __init__(
        self,
        answers: list[ExecutorResult | ExecutorError],
        *,
        kind: ExecutorKind = ExecutorKind.codex,
    ) -> None:
        self.kind = kind
        self.answers = deque(answers)
        self.calls: list[tuple[str, str, dict[str, Any], float]] = []

    def run(
        self, prompt: str, packet_text: str, schema: dict[str, Any], *, timeout_seconds: float
    ) -> ExecutorResult:
        self.calls.append((prompt, packet_text, schema, timeout_seconds))
        if not self.answers:
            raise AssertionError("FakeExecutor script exhausted")
        answer = self.answers.popleft()
        if isinstance(answer, ExecutorError):
            raise answer
        return answer


_executors: WeakKeyDictionary[ConnectionPool[Connection[DictRow]], ExtractionExecutor] = (
    WeakKeyDictionary()
)


def bind_executor(pool: ConnectionPool[Connection[DictRow]], executor: ExtractionExecutor) -> None:
    _executors[pool] = executor


_providers: WeakKeyDictionary[
    ConnectionPool[Connection[DictRow]], Callable[[], ExtractionExecutor | None]
] = WeakKeyDictionary()


def bind_provider(
    pool: ConnectionPool[Connection[DictRow]], provider: Callable[[], ExtractionExecutor | None]
) -> None:
    _providers[pool] = provider


def is_local(pool: ConnectionPool[Connection[DictRow]]) -> bool:
    return pool is not None and (pool in _providers or pool in _executors)


def executor_for(pool: ConnectionPool[Connection[DictRow]] | None) -> ExtractionExecutor | None:
    if pool is None:
        return None
    if pool in _executors:
        return _executors[pool]
    provider = _providers.get(pool)
    return provider() if provider is not None else None
