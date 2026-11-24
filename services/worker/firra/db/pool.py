from psycopg import Connection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool

from firra.db.types import cross_tenant_maintenance
from firra.settings import Settings


def create_pool(settings: Settings) -> ConnectionPool[Connection[DictRow]]:
    return create_pool_for(
        settings.database_url.get_secret_value(),
        min_size=settings.db_pool_min,
        max_size=settings.db_pool_max,
    )


def create_pool_for(
    conninfo: str, *, min_size: int = 1, max_size: int = 2
) -> ConnectionPool[Connection[DictRow]]:
    return ConnectionPool(
        conninfo=conninfo,
        min_size=min_size,
        max_size=max_size,
        # Compatible with Supabase's pooler (no server-side prepared statements).
        kwargs={"row_factory": dict_row, "prepare_threshold": None, "autocommit": False},
        connection_class=Connection[DictRow],
        open=False,
        timeout=10,
    )


@cross_tenant_maintenance("readiness probe; touches no tenant data")
def ping(pool: ConnectionPool[Connection[DictRow]]) -> bool:
    with pool.connection() as conn:
        row = conn.execute("select 1 as ok").fetchone()
    return row is not None and row["ok"] == 1
