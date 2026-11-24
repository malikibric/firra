"""Five-minute sweeps with coalesced wake/poke catch-up, without AppKit dependencies."""

import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime

from firra.logging import get_logger
from firra.maintenance.sweep import SweepCounts, run_sweep
from firra.tasks.context import TaskContext
from firra.tasks.execute import error_class_of

log = get_logger(__name__)


class LocalScheduler:
    def __init__(
        self,
        context: TaskContext,
        *,
        interval: float = 300,
        monotonic: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        sweep: Callable[[TaskContext, datetime], SweepCounts] = run_sweep,
    ) -> None:
        if interval <= 0:
            raise ValueError("interval must be positive")
        self.context = context
        self.interval = interval
        self.monotonic = monotonic
        self.wall = wall
        self.sweep = sweep
        self._condition = threading.Condition()
        self._poked = False
        self._stopping = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        with self._condition:
            if self._thread is not None:
                raise RuntimeError("scheduler already started")
            self._thread = threading.Thread(target=self._run, name="firra-sweep", daemon=True)
            self._thread.start()

    def poke(self) -> None:
        with self._condition:
            self._poked = True
            self._condition.notify_all()

    def _run(self) -> None:
        previous_mono, previous_wall = self.monotonic(), self.wall()
        deadline = previous_mono
        with self._condition:
            while not self._stopping:
                mono, wall = self.monotonic(), self.wall()
                # On macOS monotonic can pause during sleep; wall time advances. Polling
                # the difference also catches wakes without an AppKit notification.
                wake = abs((wall - previous_wall) - (mono - previous_mono)) > 2 * self.interval
                previous_mono, previous_wall = mono, wall
                if self._poked or mono >= deadline or wake:
                    self._poked = False
                    self._condition.release()
                    try:
                        self.sweep(self.context, datetime.fromtimestamp(wall, UTC))
                    except Exception as exc:
                        log.error("local.scheduler.failed", error_class=error_class_of(exc))
                    finally:
                        self._condition.acquire()
                    # Schedule from completion: an overlong sweep never causes a backlog.
                    deadline = self.monotonic() + self.interval
                    continue
                # Short checks detect wall-clock drift even if monotonic paused in sleep.
                self._condition.wait(min(1.0, max(0, deadline - mono)))

    def stop(self, wait: float = 5) -> None:
        with self._condition:
            self._stopping = True
            self._condition.notify_all()
        if self._thread is not None:
            self._thread.join(max(0, wait))
