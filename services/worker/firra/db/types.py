from collections.abc import Callable
from typing import NewType, TypeVar
from uuid import UUID

WorkspaceId = NewType("WorkspaceId", UUID)
JobId = NewType("JobId", UUID)

F = TypeVar("F", bound=Callable[..., object])


def cross_tenant_maintenance(reason: str) -> Callable[[F], F]:
    """Marks a repository function that deliberately runs without a workspace_id filter.

    Only for explicitly named admin/maintenance work (health checks, purge sweeps).
    tests/unit/test_sql_guard.py allows unscoped SQL only inside functions with this marker.
    """
    if not reason.strip():
        raise ValueError("cross_tenant_maintenance needs a reason")

    def mark(func: F) -> F:
        return func

    return mark
