"""SQL of "delete everything": empties every table of schema ``public`` and the local owner."""

import psycopg
from psycopg import sql
from psycopg.rows import DictRow
from psycopg_pool import ConnectionPool

from firra.db.types import cross_tenant_maintenance


@cross_tenant_maintenance("owner-requested deletion of the whole local database")
def empty_database(pool: ConnectionPool[psycopg.Connection[DictRow]]) -> dict[str, int]:
    with pool.connection() as conn, conn.transaction():
        tables = [
            r["tablename"]
            for r in conn.execute(
                "select tablename from pg_tables where schemaname = 'public' order by 1"
            ).fetchall()
        ]
        counts: dict[str, int] = {}
        for table in tables:
            row = conn.execute(  # nosemgrep
                sql.SQL("select count(*) as n from public.{}").format(sql.Identifier(table))
            ).fetchone()
            counts[table] = int(row["n"]) if row else 0
        if tables:
            conn.execute(  # nosemgrep
                sql.SQL("truncate table {} restart identity cascade").format(
                    sql.SQL(", ").join(
                        sql.SQL("public.{}").format(sql.Identifier(t)) for t in tables
                    )
                )
            )
        conn.execute("delete from auth.users")
    return counts
