"""Backup of the owner's non-recoverable state restores into a fresh workspace."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from firra.backup.crypto import BackupDecryptError, new_recovery_phrase
from firra.backup.service import RestoreError, export_backup, restore_backup
from firra.db.types import WorkspaceId

pytestmark = pytest.mark.integration


def test_round_trip_into_a_fresh_workspace(local_app: tuple[Any, Any, str]) -> None:
    _, ctx, conninfo = local_app
    src = WorkspaceId(ctx.identity.workspace_id)
    dst = WorkspaceId(uuid4())
    acme, bravo = uuid4(), uuid4()
    with psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as db:
        db.execute("insert into public.workspace (id, name) values (%s, 'Fresh Mac')", (dst,))
        db.execute(
            "insert into public.client (id, workspace_id, name, status, policy, mrr, currency,"
            " renewal_date, service_tags) values (%s,%s,'Acme','active','full',1500.50,'EUR',"
            "'2027-01-31','{seo}'),(%s,%s,'Bravo','churned','excluded',null,null,null,'{}')",
            (acme, src, bravo, src),
        )
        db.execute(
            "insert into public.client_domain (workspace_id, client_id, domain)"
            " values (%s,%s,'acme.test')",
            (src, acme),
        )
        db.execute(
            "insert into public.workspace_service (workspace_id, name, description, keywords)"
            " values (%s,'SEO','Synthetic','{crm,seo}')",
            (src,),
        )
        db.execute(
            "insert into public.outcome (workspace_id, client_id, event, occurred_at, notice_at)"
            " values (%s,%s,'churned','2026-09-01','2026-08-01')",
            (src, bravo),
        )
        phrase = new_recovery_phrase()
        blob = export_backup(db, src, phrase, now=datetime(2026, 10, 5, tzinfo=UTC))
        assert b"Acme" not in blob and b"acme.test" not in blob

        with pytest.raises(BackupDecryptError):
            restore_backup(db, dst, blob, "WRONG-PHRASE")
        with pytest.raises(RestoreError, match="not empty"):
            restore_backup(db, src, blob, phrase)

        result = restore_backup(db, dst, blob, phrase)
        assert result["clients"] == 2
        rows = db.execute(
            "select name, status::text, policy::text, mrr::text, currency, renewal_date::text,"
            " service_tags from public.client where workspace_id = %s order by name",
            (dst,),
        ).fetchall()
        assert [tuple(r.values()) for r in rows] == [
            ("Acme", "active", "full", "1500.50", "EUR", "2027-01-31", ["seo"]),
            ("Bravo", "churned", "excluded", None, None, None, []),
        ]
        assert db.execute(
            "select d.domain from public.client_domain d where d.workspace_id = %s", (dst,)
        ).fetchall() == [{"domain": "acme.test"}]
        assert db.execute(
            "select keywords from public.workspace_service where workspace_id = %s", (dst,)
        ).fetchall() == [{"keywords": ["crm", "seo"]}]
        outcome = db.execute(
            "select c.name, o.event::text as event, o.notice_at::text as notice"
            " from public.outcome o"
            " join public.client c on c.workspace_id = o.workspace_id and c.id = o.client_id"
            " where o.workspace_id = %s",
            (dst,),
        ).fetchall()
        assert outcome == [{"name": "Bravo", "event": "churned", "notice": "2026-08-01"}]
