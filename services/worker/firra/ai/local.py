"""Local background choice, offline CLI probes and durable action/limit status.

The settings file is read for each job. Failed sign-in/install jobs remain blocked
across engine restarts until the owner changes settings or a successful probe.
"""

import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from weakref import ReferenceType, WeakKeyDictionary, ref

from psycopg import Connection
from psycopg.rows import DictRow
from psycopg_pool import ConnectionPool

from firra.ai.claude_code import ClaudeCodeExecutor
from firra.ai.executor import ExtractionExecutor, bind_provider
from firra.db.local_ai import LocalAiRepo
from firra.db.types import WorkspaceId
from firra.local.settings import load, save
from firra.tasks.context import TaskContext
from firra_contracts import AiSettings, TaskEnvelope
from firra_contracts.generated.local.engine_status import AiStatus, Tool

ACTION_ERRORS = {"executor_not_installed": "tool_missing", "executor_not_signed_in": "needs_signin"}


class ProbedExecutor(ExtractionExecutor, Protocol):
    def detect(self) -> Tool: ...


_runtimes: WeakKeyDictionary[ConnectionPool[Connection[DictRow]], ReferenceType["AiRuntime"]] = (
    WeakKeyDictionary()
)


def runtime_for(pool: ConnectionPool[Connection[DictRow]]) -> "AiRuntime | None":
    runtime = _runtimes.get(pool) if pool is not None else None
    return runtime() if runtime is not None else None


class AiRuntime:
    def __init__(
        self,
        pool: ConnectionPool[Connection[DictRow]],
        workspace_id: WorkspaceId,
        home: Path,
        *,
        claude: Callable[[str | None], ProbedExecutor] = lambda model: ClaudeCodeExecutor(
            model=model
        ),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.pool, self.workspace_id, self.home = pool, workspace_id, home
        self.repo = LocalAiRepo(pool)
        self.claude, self.clock = claude, clock
        self.tools: list[Tool] = []
        self._unblocked_job: object | None = None
        self._lock = threading.RLock()
        reference = ref(self)
        _runtimes[pool] = reference

        def chosen() -> ExtractionExecutor | None:
            runtime = reference()
            return runtime.executor() if runtime is not None else None

        bind_provider(pool, chosen)

    def settings(self) -> AiSettings:
        return load(self.home).ai

    def update(self, settings: AiSettings) -> None:
        if settings.executor.value != "conversation_only" and not settings.background_acknowledged:
            raise ValueError("background_acknowledgement_required")
        # The optional L3-03 executor is not part of this build; never silently fall back.
        if settings.executor.value == "codex":
            raise ValueError("codex_not_available")
        with self._lock:
            owner = load(self.home)
            save(
                owner.model_copy(update={"ai": settings, "ai_revision": owner.ai_revision + 1}),
                self.home,
            )

    def executor(self) -> ExtractionExecutor | None:
        settings = self.settings()
        if settings.executor.value == "claude_code" and settings.background_acknowledged:
            return self.claude(settings.model)
        return None

    def probe(self) -> AiStatus:
        with self._lock:
            self.tools = [
                self.claude(self.settings().model).detect(),
                Tool.model_validate({"tool": "codex", "installed": False, "signed_in": None}),
            ]
            selected = self.settings().executor.value
            if any(
                t.tool.value == selected and t.installed and t.signed_in is True for t in self.tools
            ):
                job = self.repo.snapshot(workspace_id=self.workspace_id)["job"]
                self._unblocked_job = job["id"] if job else None
        return self.status()

    def _blocked(self, job: dict[str, Any] | None, revision: int) -> bool:
        return bool(
            job
            and job["status"] == "failed"
            and job["error_class"] in ACTION_ERRORS
            and job["progress"].get("ai_settings_revision", 0) == revision
            and job["id"] != self._unblocked_job
        )

    def request(self, ctx: TaskContext, trigger: str) -> bool:
        with self._lock:
            owner = load(self.home)
            if owner.ai.executor.value != "claude_code" or not owner.ai.background_acknowledged:
                return False
            snapshot = self.repo.snapshot(workspace_id=self.workspace_id)
            if self._blocked(snapshot["job"], owner.ai_revision):
                return False
            created = self.repo.request(
                workspace_id=self.workspace_id, trigger=trigger, revision=owner.ai_revision
            )
            if created is None:
                return False
            job_id, key = created
            ctx.queue.enqueue(
                TaskEnvelope(
                    workspace_id=self.workspace_id,
                    job_id=job_id,
                    idempotency_key=key,
                    type="process.records",
                    payload={"trigger": trigger},
                )
            )
            return True

    def status(self) -> AiStatus:
        owner = load(self.home)
        snapshot = self.repo.snapshot(workspace_id=self.workspace_id)
        job = snapshot["job"]
        state, error, resume = "idle", None, None
        if owner.ai.executor.value == "conversation_only":
            state = "conversation_only"
        elif self._blocked(job, owner.ai_revision):
            error = job["error_class"]
            state = ACTION_ERRORS[error]
        elif job and job["status"] in ("queued", "running"):
            progress = job["progress"]
            at = progress.get("retry_at") or progress.get("resume_at")
            if at and datetime.fromisoformat(at) > self.clock():
                state, resume, error = (
                    "waiting_for_limit",
                    progress.get("resume_at"),
                    "executor_limit_reached",
                )
            else:
                state = "working"
        elif (
            job
            and job["status"] == "failed"
            and job["error_class"] not in ACTION_ERRORS
            and job["progress"].get("ai_settings_revision", 0) == owner.ai_revision
        ):
            state, error = "error", job["error_class"]
        return AiStatus.model_validate(
            {
                "executor": owner.ai.executor.value,
                "tools": self.tools,
                "queue": snapshot["queue"],
                "state": state,
                "resume_at": resume,
                "error_class": error,
            }
        )
