"""Loads the minimization Directory (client domains, firm domains) for one workspace."""

from uuid import UUID

from psycopg import Connection
from psycopg.rows import DictRow
from psycopg_pool import ConnectionPool

from firra.db.types import WorkspaceId
from firra.policy.minimize import ClientRef, Directory

_CLIENT_DOMAINS = """
select d.domain, c.id, c.name, c.policy::text as policy
  from public.client_domain d
  join public.client c on c.workspace_id = d.workspace_id and c.id = d.client_id
 where d.workspace_id = %(workspace_id)s
"""

# The firm's own domains: members, contributors and connected mailboxes, minus public providers.
_FIRM_DOMAINS = """
with emails as (
  select lower(u.email) as email
    from public.membership m join auth.users u on u.id = m.user_id
   where m.workspace_id = %(workspace_id)s
  union
  select email from public.contributor
   where workspace_id = %(workspace_id)s and status <> 'revoked'
  union
  select lower(account_label) from public.connection
   where workspace_id = %(workspace_id)s and mode <> 'upload' and account_label like '%%@%%'
)
select distinct split_part(email, '@', 2) as domain
  from emails
 where email like '%%@%%' and not private.is_public_email_domain(split_part(email, '@', 2))
"""

_SETTINGS = "select internal_mentions from public.workspace where id = %(workspace_id)s"


def load_directory(
    pool: ConnectionPool[Connection[DictRow]], *, workspace_id: WorkspaceId
) -> Directory:
    params = {"workspace_id": workspace_id}
    with pool.connection() as conn:
        clients = conn.execute(_CLIENT_DOMAINS, params).fetchall()
        firm = conn.execute(_FIRM_DOMAINS, params).fetchall()
        row = conn.execute(_SETTINGS, params).fetchone()
    refs: dict[UUID, ClientRef] = {}
    domains: dict[str, ClientRef] = {}
    for r in clients:
        ref = refs.setdefault(r["id"], ClientRef(id=r["id"], name=r["name"], policy=r["policy"]))
        domains[r["domain"]] = ref
    return Directory(
        client_domains=domains,
        firm_domains=frozenset(r["domain"] for r in firm),
        internal_mentions=bool(row and row["internal_mentions"]),
    )
