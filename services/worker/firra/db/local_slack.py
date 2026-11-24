"""Workspace-scoped Slack setup, mappings and atomic minimized ingestion.

Reuse ingest's record upsert and encryption; never persist tokens or unmapped people.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from firra.db.records import _UPSERT_RECORD, RecordInput
from firra.db.types import WorkspaceId
from firra.local.bodycrypt import write_body


def connect(
    conn: Connection[DictRow],
    *,
    workspace_id: WorkspaceId,
    user_id: UUID,
    connection_id: UUID,
    account_label: str,
    team_id: str,
    slack_user_id: str,
    workspace_url: str | None,
) -> UUID | None:
    row = conn.execute(
        "insert into public.connection (id,workspace_id,provider,mode,owner_user_id,"
        "account_label,external_account_id,cursor) "
        "values (%(id)s,%(workspace_id)s,'slack','user',%(user)s,%(label)s,%(account)s,%(cursor)s) "
        "on conflict (id) do update set status='active',last_error_class=null,"
        "account_label=excluded.account_label,cursor=connection.cursor || excluded.cursor "
        "where connection.workspace_id=%(workspace_id)s and connection.provider='slack' "
        "and connection.external_account_id=excluded.external_account_id returning id",
        {
            "id": connection_id,
            "workspace_id": workspace_id,
            "user": user_id,
            "label": account_label,
            "account": slack_user_id,
            "cursor": Jsonb(
                {"team_id": team_id, "user_id": slack_user_id, "workspace_url": workspace_url}
            ),
        },
    ).fetchone()
    return row["id"] if row else None


def export_connection(
    conn: Connection[DictRow], *, workspace_id: WorkspaceId, team_id: str
) -> UUID | None:
    """Select the same workspace/team connection so API and export share external ids."""
    rows = conn.execute(
        "select id from public.connection where workspace_id=%(workspace_id)s "
        "and provider='slack' and status='active' and mode in ('user','upload') "
        "and cursor->>'team_id'=%(team)s order by id limit 2",
        {"workspace_id": workspace_id, "team": team_id},
    ).fetchall()
    if len(rows) > 1:
        raise ValueError("ambiguous_slack_connection")
    return rows[0]["id"] if rows else None


def open_export_connection(
    conn: Connection[DictRow], *, workspace_id: WorkspaceId, user_id: UUID, team_id: str
) -> UUID:
    """Create the brief's token-free upload connection when no API connection exists."""
    conn.execute(
        "select pg_advisory_xact_lock(hashtextextended(%(key)s,0))",
        {"key": f"slack-export:{workspace_id}:{team_id}"},
    )
    existing = export_connection(conn, workspace_id=workspace_id, team_id=team_id)
    if existing is not None:
        return existing
    row = conn.execute(
        "insert into public.connection (workspace_id,provider,mode,owner_user_id,"
        "account_label,external_account_id,cursor) "
        "values (%(workspace_id)s,'slack','upload',%(user)s,'Slack export',%(team)s,%(cursor)s) "
        "on conflict (workspace_id,provider,mode,external_account_id) "
        "where external_account_id is not null "
        "do update set account_label=connection.account_label "
        "where connection.status='active' returning id",
        {
            "workspace_id": workspace_id,
            "user": user_id,
            "team": team_id,
            "cursor": Jsonb({"team_id": team_id}),
        },
    ).fetchone()
    if row is None:
        raise ValueError("slack_connection_unavailable")
    connection_id: UUID = row["id"]
    return connection_id


def disconnect(
    conn: Connection[DictRow], *, workspace_id: WorkspaceId, connection_id: UUID
) -> None:
    args = {"workspace_id": workspace_id, "id": connection_id}
    conn.execute(
        "update public.connection set status='revoked',last_error_class='disconnected' "
        "where workspace_id=%(workspace_id)s and id=%(id)s and provider='slack'",
        args,
    )
    conn.execute(
        "update public.job set status='failed',error_class='connection_unavailable',"
        "finished_at=now() where workspace_id=%(workspace_id)s and connection_id=%(id)s "
        "and status in ('queued','running')",
        args,
    )


def set_scope(
    conn: Connection[DictRow],
    *,
    workspace_id: WorkspaceId,
    connection_id: UUID,
    channel_id: str,
    label: str | None,
    client_id: UUID | None,
    include: bool,
    meta: dict[str, Any],
) -> None:
    conn.execute(
        "insert into public.connection_scope (workspace_id,connection_id,kind,external_id,"
        "label,client_id,include,meta) values (%(workspace_id)s,%(connection)s,'channel',"
        "%(channel)s,%(label)s,%(client)s,%(include)s,%(meta)s) "
        "on conflict (connection_id,kind,external_id) do update set label=excluded.label,"
        "client_id=excluded.client_id,include=excluded.include,meta=excluded.meta",
        {
            "workspace_id": workspace_id,
            "connection": connection_id,
            "channel": channel_id,
            "label": label,
            "client": client_id,
            "include": include,
            "meta": Jsonb(meta),
        },
    )


def active(conn: Connection[DictRow], *, workspace_id: WorkspaceId) -> list[UUID]:
    rows = conn.execute(
        "select id from public.connection where workspace_id=%(workspace_id)s "
        "and provider='slack' and mode='user' and status='active'",
        {"workspace_id": workspace_id},
    ).fetchall()
    return [r["id"] for r in rows]


def clients(conn: Connection[DictRow], *, workspace_id: WorkspaceId) -> list[dict[str, Any]]:
    return conn.execute(
        "select c.id,c.name,c.policy::text,coalesce(array_agg(d.domain) "
        "filter(where d.domain is not null),'{}') as domains from public.client c "
        "left join public.client_domain d on d.workspace_id=%(workspace_id)s and d.client_id=c.id "
        "where c.workspace_id=%(workspace_id)s group by c.id order by c.name",
        {"workspace_id": workspace_id},
    ).fetchall()


class SlackRepo:
    def __init__(self, pool: ConnectionPool[Connection[DictRow]]) -> None:
        self.pool = pool

    @contextmanager
    def syncing(self, *, workspace_id: WorkspaceId, connection_id: UUID) -> Iterator[bool]:
        with self.pool.connection() as conn:
            args = {"key": f"slack:{workspace_id}:{connection_id}"}
            row = conn.execute(
                "select pg_try_advisory_lock(hashtextextended(%(key)s,0)) as locked", args
            ).fetchone()
            locked = bool(row and row["locked"])
            try:
                yield locked
            finally:
                if locked:
                    conn.execute("select pg_advisory_unlock(hashtextextended(%(key)s,0))", args)

    def save_channel(
        self, *, workspace_id: WorkspaceId, connection_id: UUID, channel: str, state: dict[str, Any]
    ) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "update public.connection set cursor=jsonb_set(cursor,'{slack_channels}',"
                "coalesce(cursor->'slack_channels','{}') || "
                "jsonb_build_object(%(channel)s::text,%(state)s::jsonb),true),"
                "last_synced_at=now(),last_error_class=null "
                "where workspace_id=%(workspace_id)s and id=%(id)s "
                "and status='active' and provider='slack'",
                {
                    "workspace_id": workspace_id,
                    "id": connection_id,
                    "channel": channel,
                    "state": Jsonb(state),
                },
            )

    def mappings(self, *, workspace_id: WorkspaceId, connection_id: UUID) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            return conn.execute(
                "select s.external_id,s.label,s.meta,s.client_id,c.policy::text,c.name "
                "from public.connection_scope s join public.client c "
                "on c.workspace_id=%(workspace_id)s and c.id=s.client_id "
                "where s.workspace_id=%(workspace_id)s and s.connection_id=%(id)s "
                "and s.kind='channel' and s.include and c.policy<>'excluded' "
                "order by s.external_id",
                {"workspace_id": workspace_id, "id": connection_id},
            ).fetchall()

    def mapped_person(self, *, workspace_id: WorkspaceId, slack_id: str) -> dict[str, Any] | None:
        with self.pool.connection() as conn:
            return conn.execute(
                "select c.id,c.name,c.policy::text from public.person_identity i "
                "join public.person p on p.workspace_id=%(workspace_id)s and p.id=i.person_id "
                "join public.client c on c.workspace_id=%(workspace_id)s and c.id=p.client_id "
                "where i.workspace_id=%(workspace_id)s and i.kind='slack_id' "
                "and i.value=%(value)s and p.side='client'",
                {"workspace_id": workspace_id, "value": slack_id},
            ).fetchone()

    def existing(
        self,
        *,
        workspace_id: WorkspaceId,
        connection_id: UUID,
        channel: str,
        start: datetime,
        end: datetime,
    ) -> list[str]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "select external_id from public.source_record where workspace_id=%(workspace_id)s "
                "and connection_id=%(id)s and provider='slack' "
                "and metadata->>'channel_id'=%(channel)s and occurred_at>=%(start)s "
                "and occurred_at<%(end)s",
                {
                    "workspace_id": workspace_id,
                    "id": connection_id,
                    "channel": channel,
                    "start": start,
                    "end": end,
                },
            ).fetchall()
        return [r["external_id"] for r in rows]

    def store(
        self,
        *,
        workspace_id: WorkspaceId,
        record: RecordInput,
        slack_id: str,
        name: str | None,
        email: str | None,
        side: Literal["client", "firm"],
        require_scope: bool = True,
    ) -> tuple[UUID, bool] | None:
        with self.pool.connection() as conn, conn.transaction():
            policy = conn.execute(
                "select policy::text from public.client where workspace_id=%(workspace_id)s "
                "and id=%(client)s for share",
                {"workspace_id": workspace_id, "client": record.client_id},
            ).fetchone()
            if policy is None or policy["policy"] == "excluded":
                return None
            if require_scope:
                scope = conn.execute(
                    "select id from public.connection_scope where workspace_id=%(workspace_id)s "
                    "and connection_id=%(connection)s and kind='channel' "
                    "and external_id=%(channel)s "
                    "and client_id=%(client)s and include for share",
                    {
                        "workspace_id": workspace_id,
                        "connection": record.connection_id,
                        "channel": record.metadata["channel_id"],
                        "client": record.client_id,
                    },
                ).fetchone()
                if scope is None:
                    return None
            # Serialise writers for identity resolution and the final encryption row id.
            connection = conn.execute(
                "select id from public.connection where workspace_id=%(workspace_id)s "
                "and id=%(id)s and provider='slack' and status='active' for update",
                {"workspace_id": workspace_id, "id": record.connection_id},
            ).fetchone()
            if connection is None:
                return None
            person_id = _person(
                conn,
                workspace_id=workspace_id,
                slack_id=slack_id,
                email=email,
                name=name,
                side=side,
                client_id=record.client_id,
                seen_at=record.occurred_at,
            )
            existing = conn.execute(
                "select id from public.source_record where workspace_id=%(workspace_id)s "
                "and connection_id=%(connection)s and external_id=%(external)s",
                {
                    "workspace_id": workspace_id,
                    "connection": record.connection_id,
                    "external": record.external_id,
                },
            ).fetchone()
            record_id = existing["id"] if existing else uuid4()
            params = {
                **asdict(record),
                "workspace_id": workspace_id,
                "record_id": record_id,
                "author_person_id": person_id,
                "participants": [person_id],
                "processing_state": "pending"
                if policy["policy"] == "full" and record.body_clean
                else "not_applicable",
            }
            params["policy"] = policy["policy"]
            if policy["policy"] != "full":
                params.update(
                    body_clean=None,
                    content_hash=None,
                    deep_link=None,
                    metadata=Jsonb({"channel_id": record.metadata["channel_id"]}),
                )
            else:
                params["metadata"] = Jsonb(record.metadata)
                params["body_clean"] = write_body(
                    self.pool, workspace_id, record_id, record.body_clean
                )
            row = conn.execute(_UPSERT_RECORD, params).fetchone()
            return (row["id"], bool(row["inserted"])) if row else None


def _person(
    conn: Connection[DictRow],
    *,
    workspace_id: WorkspaceId,
    slack_id: str,
    email: str | None,
    name: str | None,
    side: str,
    client_id: UUID,
    seen_at: datetime,
) -> UUID:
    args = {
        "workspace_id": workspace_id,
        "slack": slack_id,
        "email": email.lower() if email else None,
        "name": name,
        "side": side,
        "client": client_id if side == "client" else None,
        "seen": seen_at,
    }
    row = conn.execute(
        "select p.id from public.person_identity i join public.person p "
        "on p.workspace_id=%(workspace_id)s and p.id=i.person_id "
        "where i.workspace_id=%(workspace_id)s and ((i.kind='slack_id' and i.value=%(slack)s) "
        "or (i.kind='email' and i.value=%(email)s)) order by (i.kind='slack_id') desc limit 1",
        args,
    ).fetchone()
    if row is None:
        row = conn.execute(
            "insert into public.person "
            "(workspace_id,client_id,side,display_name,first_seen_at,last_seen_at) "
            "values (%(workspace_id)s,%(client)s,%(side)s,%(name)s,%(seen)s,%(seen)s) returning id",
            args,
        ).fetchone()
    assert row is not None  # noqa: S101 - insert returns its row
    person_id: UUID = row["id"]
    args["person"] = person_id
    conn.execute(
        "update public.person set last_seen_at=greatest(coalesce(last_seen_at,%(seen)s),%(seen)s), "
        "first_seen_at=least(coalesce(first_seen_at,%(seen)s),%(seen)s) "
        "where workspace_id=%(workspace_id)s and id=%(person)s",
        args,
    )
    for kind, value in (("slack_id", slack_id), ("email", email.lower() if email else None)):
        if value:
            conn.execute(
                "insert into public.person_identity (workspace_id,person_id,kind,value) "
                "values (%(workspace_id)s,%(person)s,%(kind)s,%(value)s) "
                "on conflict (workspace_id,kind,value) do nothing",
                {**args, "kind": kind, "value": value},
            )
    return person_id
