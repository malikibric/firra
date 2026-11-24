class PermanentTaskError(Exception):
    """A failure that retrying cannot fix; the job is failed immediately with `error_class`."""

    def __init__(self, error_class: str) -> None:
        super().__init__(error_class)
        self.error_class = error_class


class ContinueTask(Exception):  # noqa: N818 - control flow signal, not an error
    """Raised by a handler that ran out of its time budget with work left, or has to wait.

    The resume point must already be in ``progress``; the router stores it, re-queues the job and
    enqueues the same task again (after ``delay_seconds``), which continues from ``job.progress``.
    """

    def __init__(self, progress: dict[str, object], delay_seconds: float = 0) -> None:
        super().__init__("continue")
        self.progress = progress
        self.delay_seconds = delay_seconds
