"""Local retention: delete the text of old records, keep what was derived from it.

Text means the encrypted body, the mail subject and the meeting title. Dates, authors, word counts,
verified evidence quotes and all derived facts stay (``CLAUDE.md`` section 6). A record that was
never processed can no longer be, so it ends as ``skipped_purged``.
"""

from datetime import datetime

import psycopg
from psycopg.rows import DictRow
from psycopg_pool import ConnectionPool

from firra.db.types import cross_tenant_maintenance


@cross_tenant_maintenance("owner-chosen retention of the one local owner's data")
def purge_text_before(pool: ConnectionPool[psycopg.Connection[DictRow]], cutoff: datetime) -> int:
    with pool.connection() as conn:
        row = conn.execute(
            """with purged as (
                 update public.source_record
                    set body_clean = null,
                        body_purged_at = coalesce(body_purged_at, now()),
                        metadata = metadata - 'subject' - 'title',
                        processing_state = case when processing_state = 'pending'
                                                then 'skipped_purged' else processing_state end
                  where occurred_at < %(cutoff)s
                    and (body_clean is not null or metadata ?| array['subject', 'title'])
                  returning 1)
               select count(*)::int as n from purged""",
            {"cutoff": cutoff},
        ).fetchone()
    return int(row["n"]) if row else 0
