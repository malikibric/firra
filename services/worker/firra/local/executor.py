"""In-process task delivery: delayed heap, bounded workers, persisted restart recovery."""

import heapq
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from itertools import count
from uuid import UUID

from firra.db.local_jobs import LocalJobs
from firra.logging import get_logger
from firra.tasks.context import TaskContext
from firra.tasks.execute import error_class_of, execute_envelope
from firra_contracts import TaskEnvelope

log = get_logger(__name__)
# Legacy cloud probe is not idempotent and is deleted in F9; never repeat its provider calls.
NON_RESUMABLE = frozenset({"diagnostics.llm_probe"})
# The owner waits on these on screen: they get one slot of their own and go ahead of background
# work (a month of mail can occupy every regular worker for minutes).
INTERACTIVE = frozenset({"onboarding.suggest"})
# ponytail: interactive jobs sort an hour earlier instead of a second queue; an interactive retry in
# backoff can delay background jobs by at most its backoff (<= 5 min).
_AHEAD_SECONDS = 3600.0
JobKey = tuple[UUID, UUID]


class LocalExecutor:
    def __init__(
        self, context: TaskContext, *, workers: int = 2, clock: Callable[[], float] = time.monotonic
    ) -> None:
        if workers < 1:
            raise ValueError("workers must be positive")
        self.context = replace(context, queue=self)
        self.jobs = LocalJobs(context.pool)
        self.clock = clock
        self.workers = workers
        self._condition = threading.Condition()
        self._heap: list[tuple[float, int, float, TaskEnvelope]] = []
        self._sequence = count()
        self._pending: set[JobKey] = set()
        self._active: set[JobKey] = set()
        self._interactive: set[JobKey] = set()
        self._stopping = False
        self._thread: threading.Thread | None = None
        self._pool: ThreadPoolExecutor | None = None

    def enqueue(self, envelope: TaskEnvelope, *, delay_seconds: float = 0) -> None:
        self._schedule(self.jobs.ensure(envelope), delay_seconds)

    def _schedule(self, envelope: TaskEnvelope, delay: float) -> None:
        key = (envelope.workspace_id, envelope.job_id)
        with self._condition:
            if key not in self._pending:
                self._pending.add(key)
                due = self.clock() + max(0, delay)
                ahead = _AHEAD_SECONDS if envelope.type in INTERACTIVE else 0.0
                heapq.heappush(self._heap, (due - ahead, next(self._sequence), due, envelope))
            self._condition.notify_all()

    def start(self) -> None:
        with self._condition:
            if self._thread is not None:
                raise RuntimeError("executor already started")
            for envelope in self.jobs.recover(NON_RESUMABLE):
                self._schedule(envelope, 0)
            self._pool = ThreadPoolExecutor(
                max_workers=self.workers + 1, thread_name_prefix="firra-job"
            )
            self._thread = threading.Thread(
                target=self._dispatch, name="firra-delivery", daemon=True
            )
            self._thread.start()

    def _dispatch(self) -> None:
        with self._condition:
            while not self._stopping:
                deferred = []
                ready = None
                while self._heap and len(self._active) < self.workers + 1:
                    entry = self._heap[0]
                    if entry[2] > self.clock():
                        break
                    envelope = entry[3]
                    interactive = envelope.type in INTERACTIVE
                    if not interactive and self._background() >= self.workers:
                        break  # the extra slot is kept for the owner's interactive work
                    heapq.heappop(self._heap)
                    key = (envelope.workspace_id, envelope.job_id)
                    if key in self._active:
                        deferred.append(entry)
                    else:
                        self._pending.remove(key)
                        self._active.add(key)
                        if interactive:
                            self._interactive.add(key)
                        ready = envelope
                        break
                for entry in deferred:
                    heapq.heappush(self._heap, entry)
                if ready is not None and self._pool is not None:
                    self._pool.submit(self._run, ready)
                    continue
                timeout = None
                future = [
                    due
                    for _, _, due, env in self._heap
                    if (env.workspace_id, env.job_id) not in self._active
                ]
                if future and len(self._active) < self.workers + 1:
                    timeout = max(0, min(future) - self.clock())
                self._condition.wait(timeout)

    def _background(self) -> int:
        return len(self._active) - len(self._interactive)

    def _run(self, envelope: TaskEnvelope) -> None:
        started = self.clock()
        try:
            result = execute_envelope(
                self.context.jobs,
                self.context,
                envelope,
                requeue=lambda env, delay: self.enqueue(env, delay_seconds=delay),
            )
            if result.status == "retry":
                self._schedule(envelope, min(5 * 2 ** min(result.attempt - 1, 6), 300))
        except Exception as exc:
            # Infrastructure failures leave persisted state for startup recovery.
            log.error(
                "local.executor.failed",
                task_type=envelope.type,
                job_id=envelope.job_id,
                error_class=error_class_of(exc),
            )
        finally:
            log.info(
                "local.executor.finished",
                task_type=envelope.type,
                job_id=envelope.job_id,
                duration_ms=round((self.clock() - started) * 1000),
            )
            with self._condition:
                self._active.remove((envelope.workspace_id, envelope.job_id))
                self._interactive.discard((envelope.workspace_id, envelope.job_id))
                self._condition.notify_all()

    def stop(self, wait: float = 30) -> None:
        deadline = time.monotonic() + max(0, wait)
        with self._condition:
            self._stopping = True
            self._condition.notify_all()
            while self._active and time.monotonic() < deadline:
                self._condition.wait(deadline - time.monotonic())
        if self._thread is not None:
            self._thread.join(max(0, deadline - time.monotonic()))
        if self._pool is not None:
            self._pool.shutdown(wait=False)
