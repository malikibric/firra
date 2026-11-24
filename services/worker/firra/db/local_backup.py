"""SQL of the backup: read the owner's non-recoverable state, restore it into an empty workspace.

Backup is the owner's own data; it runs on the superuser connection of the engine at an explicit
request of the owner (never from a route that takes a free path). Restore generates new ids and
remaps references inside the file, so it works on a new Mac with a new workspace.
"""

from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from firra.db.types import WorkspaceId

CLIENT_COLUMNS = (
    "name, status::text as status, policy::text as policy, mrr, currency, contract_start,"
    " renewal_date, notice_period_days, last_price_increase_at, churned_at, service_tags"
)


def read_state(conn: psycopg.Connection[DictRow], workspace_id: WorkspaceId) -> dict[str, Any]:
    ws = {"ws": workspace_id}
    clients = conn.execute(
        f"select id, {CLIENT_COLUMNS} from public.client"  # noqa: S608
        " where workspace_id = %(ws)s order by name, id",
        ws,
    ).fetchall()
    return {
        "clients": clients,
        "domains": conn.execute(
            "select client_id, domain from public.client_domain where workspace_id = %(ws)s"
            " order by domain",
            ws,
        ).fetchall(),
        "services": conn.execute(
            "select name, description, keywords from public.workspace_service"
            " where workspace_id = %(ws)s order by name",
            ws,
        ).fetchall(),
        "routines": conn.execute(
            "select kind::text as kind, enabled, params from public.routine"
            " where workspace_id = %(ws)s order by kind",
            ws,
        ).fetchall(),
        "outcomes": conn.execute(
            "select client_id, event::text as event, occurred_at, notice_at, first_known_at,"
            " amount_change, currency, source::text as source, note"
            " from public.outcome where workspace_id = %(ws)s order by occurred_at, id",
            ws,
        ).fetchall(),
        # Mappings and feedback point at rows that do not exist on a new Mac (connections, cards):
        # they are exported by stable keys and applied after the first resync.
        "scopes": conn.execute(
            "select c.provider::text as provider, s.kind::text as kind, s.external_id,"
            " s.client_id, s.include, s.label"
            " from public.connection_scope s join public.connection c"
            " on c.workspace_id = s.workspace_id and c.id = s.connection_id"
            " where s.workspace_id = %(ws)s order by c.provider, s.external_id",
            ws,
        ).fetchall(),
        "feedback": conn.execute(
            "select d.client_id, d.kind::text as kind, d.dedupe_key, f.verdict::text as verdict,"
            " f.action::text as action, f.note, f.created_at"
            " from public.decision_feedback f join public.decision_card d"
            " on d.workspace_id = f.workspace_id and d.id = f.decision_card_id"
            " where f.workspace_id = %(ws)s order by f.created_at, f.id",
            ws,
        ).fetchall(),
    }


def workspace_is_empty(conn: psycopg.Connection[DictRow], workspace_id: WorkspaceId) -> bool:
    row = conn.execute(
        "select (select count(*) from public.client where workspace_id = %(ws)s)"
        " + (select count(*) from public.workspace_service where workspace_id = %(ws)s) as n",
        {"ws": workspace_id},
    ).fetchone()
    return row is not None and row["n"] == 0


def restore_state(
    conn: psycopg.Connection[DictRow], workspace_id: WorkspaceId, state: dict[str, Any]
) -> dict[UUID, UUID]:
    """Inserts the stable tables with new ids; returns old client id to new client id."""
    ids: dict[UUID, UUID] = {}
    for c in state["clients"]:
        new = uuid4()
        ids[UUID(str(c["id"]))] = new
        conn.execute(
            "insert into public.client (id, workspace_id, name, status, policy, mrr, currency,"
            " contract_start, renewal_date, notice_period_days, last_price_increase_at,"
            " churned_at, service_tags) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
            " %s)",
            (
                new,
                workspace_id,
                c["name"],
                c["status"],
                c["policy"],
                c["mrr"],
                c["currency"],
                c["contract_start"],
                c["renewal_date"],
                c["notice_period_days"],
                c["last_price_increase_at"],
                c["churned_at"],
                c["service_tags"],
            ),
        )
    for d in state["domains"]:
        conn.execute(
            "insert into public.client_domain (workspace_id, client_id, domain)"
            " values (%s, %s, %s)",
            (workspace_id, ids[UUID(str(d["client_id"]))], d["domain"]),
        )
    for s in state["services"]:
        conn.execute(
            "insert into public.workspace_service (workspace_id, name, description, keywords)"
            " values (%s, %s, %s, %s)",
            (workspace_id, s["name"], s["description"], s["keywords"]),
        )
    for r in state["routines"]:
        conn.execute(
            "insert into public.routine (workspace_id, kind, enabled, params) values"
            " (%s, %s, %s, %s) on conflict (workspace_id, kind)"
            " do update set enabled = excluded.enabled, params = excluded.params",
            (workspace_id, r["kind"], r["enabled"], Jsonb(r["params"])),
        )
    for o in state["outcomes"]:
        conn.execute(
            "insert into public.outcome (workspace_id, client_id, event, occurred_at, notice_at,"
            " first_known_at, amount_change, currency, source, note)"
            " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                workspace_id,
                ids[UUID(str(o["client_id"]))],
                o["event"],
                o["occurred_at"],
                o["notice_at"],
                o["first_known_at"],
                o["amount_change"],
                o["currency"],
                o["source"],
                o["note"],
            ),
        )
    return ids
