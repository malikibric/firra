"""The single local owner (Local v1): one `auth.users` row and one workspace at first start."""

from dataclasses import dataclass
from uuid import UUID

import psycopg

from firra.db.types import cross_tenant_maintenance

OWNER_EMAIL = "owner@firra.local"


@dataclass(frozen=True)
class LocalIdentity:
    user_id: UUID
    workspace_id: UUID
    email: str = OWNER_EMAIL


@cross_tenant_maintenance("the local database holds exactly one owner; looking it up has no tenant")
def find_owner(conn: psycopg.Connection) -> LocalIdentity | None:
    row = conn.execute(
        """select m.user_id, m.workspace_id from public.membership m
           join auth.users u on u.id = m.user_id
           where m.role = 'owner' and u.email = %s order by m.created_at limit 1""",
        (OWNER_EMAIL,),
    ).fetchone()
    return None if row is None else LocalIdentity(user_id=row[0], workspace_id=row[1])


@cross_tenant_maintenance("creating the one local owner and workspace at first start")
def create_owner(conn: psycopg.Connection, *, workspace_name: str, timezone: str) -> LocalIdentity:
    user = conn.execute(
        """insert into auth.users (instance_id, aud, role, email, email_confirmed_at)
           values ('00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated',
                   %s, now())
           on conflict do nothing returning id""",
        (OWNER_EMAIL,),
    ).fetchone()
    if user is None:
        user = conn.execute("select id from auth.users where email = %s", (OWNER_EMAIL,)).fetchone()
    assert user is not None  # noqa: S101 - the insert or the select must have produced the row
    user_id: UUID = user[0]
    # create_workspace is a caller RPC: run it as the owner, exactly as the cloud app did.
    conn.execute("set local role authenticated")
    conn.execute("select set_config('request.jwt.claim.sub', %s, true)", (str(user_id),))
    row = conn.execute(
        "select public.create_workspace(%s, %s)", (workspace_name, timezone)
    ).fetchone()
    conn.execute("reset role")
    assert row is not None  # noqa: S101
    return LocalIdentity(user_id=user_id, workspace_id=row[0])
