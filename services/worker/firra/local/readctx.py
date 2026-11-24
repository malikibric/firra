"""What every read model and write needs: the pool, who the local owner is, a clock.

Reads and writes run **as the signed-in owner**: inside one transaction the session switches to
the ``authenticated`` role and carries the owner's id in ``request.jwt.claim.sub``, so the same
row-level security policies and caller RPCs that guarded the cloud API guard the local one
(excluded clients stay invisible, ``metadata_only`` clients have no quotes). The engine never
reads client data as superuser.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from psycopg import Connection
from psycopg.rows import DictRow
from psycopg_pool import ConnectionPool

from firra.db.local_identity import LocalIdentity


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class ReadContext:
    pool: ConnectionPool[Connection[DictRow]]
    identity: LocalIdentity
    clock: Callable[[], datetime] = field(default=_now)
    home: Path | None = None
    engine_version: str = "dev"

    @contextmanager
    def caller(self) -> Iterator[Connection[DictRow]]:
        """A transaction that runs as the owner (role ``authenticated``, RLS enforced)."""
        with self.pool.connection() as conn, conn.transaction():
            conn.execute("set local role authenticated")
            conn.execute(
                "select set_config('request.jwt.claim.sub', %s, true)",
                (str(self.identity.user_id),),
            )
            yield conn
