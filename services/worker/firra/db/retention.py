"""Hourly event-driven purge: processed/delivered, 72-hour fallback, 7-day cap.

The 7-day cap holds for every row, whatever its state (a running extraction only saves while the
body is still there, so a purge cannot be undone by it).
Rehydration restarts the retention clock from the later of created_at and rehydrated_at.
Raw deletion is best effort; the 7-day bucket lifecycle is the backstop.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Final

from psycopg import Connection
from psycopg.rows import DictRow
from psycopg_pool import ConnectionPool

from firra.db.types import cross_tenant_maintenance
from firra.logging import get_logger
from firra.storage.raw import RawStore

MAX_RETENTION: Final = timedelta(days=7)
UNDELIVERED_RETENTION: Final = timedelta(hours=72)
log = get_logger(__name__)
BATCH_SIZE: Final = 5000


@dataclass(frozen=True, slots=True)
class PurgeResult:
    purged: int
    # Bodies purged before extraction reached them (retention does not wait; P2 D7).
    unprocessed: int
    undelivered: int


@cross_tenant_maintenance("event-driven raw retention applies to every workspace")
def purge_expired_bodies(pool: ConnectionPool[Connection[DictRow]], raw: RawStore) -> PurgeResult:
    """Purges in batches so locks stay short."""
    purge_batch = """
    with due as (
      select id, raw_object_uri, processing_state = 'pending' as unprocessed,
             vault_user_id is not null and vault_delivered_at is null as undelivered
        from public.source_record
       where (body_clean is not null or raw_object_uri is not null)
         and (greatest(created_at, coalesce(rehydrated_at, created_at)) < now() - %(maximum)s
              or (processing_state in ('done', 'not_applicable')
                  and (vault_delivered_at is not null or vault_user_id is null
                       or greatest(created_at, coalesce(rehydrated_at, created_at))
                          < now() - %(undelivered)s)))
       limit %(batch)s
       for update skip locked
    )
    update public.source_record r
       set body_clean = null, raw_object_uri = null,
           body_purged_at = coalesce(r.body_purged_at, now()),
           processing_state = case when due.unprocessed then 'skipped_purged'
                                   else r.processing_state end
      from due
     where r.id = due.id
    returning r.id, due.raw_object_uri, due.unprocessed, due.undelivered
    """
    purged = unprocessed = undelivered = 0
    while True:
        with pool.connection() as conn:
            rows = conn.execute(
                purge_batch,
                {
                    "maximum": MAX_RETENTION,
                    "undelivered": UNDELIVERED_RETENTION,
                    "batch": BATCH_SIZE,
                },
            ).fetchall()
            for row in rows:
                if row["raw_object_uri"] is not None:
                    try:
                        raw.delete(row["raw_object_uri"])
                    except Exception as error:
                        log.warning(
                            "maintenance.raw_delete_failed",
                            record_id=row["id"],
                            error_class=type(error).__name__,
                        )
        purged += len(rows)
        unprocessed += sum(1 for row in rows if row["unprocessed"])
        undelivered += sum(1 for row in rows if row["undelivered"])
        if len(rows) < BATCH_SIZE:
            return PurgeResult(purged=purged, unprocessed=unprocessed, undelivered=undelivered)
