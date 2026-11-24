"""Start-time repair of data written before the local profile protected it (source-layer S0).

Older engines wrote mail subjects and meeting titles to ``source_record.metadata`` in plain text
and pointed ``raw_object_uri`` at plain files. This re-encrypts those fields with the body key and
clears the pointers; the engine deletes the files themselves.
"""

from uuid import UUID

import psycopg
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from firra.db.types import cross_tenant_maintenance
from firra.local.bodycrypt import ENCRYPTED_FIELDS, PREFIX, write_field


@cross_tenant_maintenance("the local database belongs to one owner; repairs every row once")
def protect_legacy_fields(pool: ConnectionPool[psycopg.Connection[DictRow]]) -> int:
    with pool.connection() as conn, conn.transaction():
        conn.execute(
            "update public.source_record set raw_object_uri = null where raw_object_uri is not null"
        )
        rows = conn.execute(
            "select id, workspace_id, metadata from public.source_record"
            " where (metadata ? 'subject' and metadata->>'subject' not like %(p)s)"
            "    or (metadata ? 'title' and metadata->>'title' not like %(p)s)",
            {"p": PREFIX + "%"},
        ).fetchall()
        for row in rows:
            record_id = UUID(str(row["id"]))
            metadata = dict(row["metadata"])
            for field in ENCRYPTED_FIELDS:
                value = metadata.get(field)
                if isinstance(value, str) and not value.startswith(PREFIX):
                    metadata[field] = write_field(
                        pool, row["workspace_id"], record_id, field, value
                    )
            conn.execute(
                "update public.source_record set metadata = %s where workspace_id = %s and id = %s",
                (Jsonb(metadata), row["workspace_id"], record_id),
            )
    return len(rows)
