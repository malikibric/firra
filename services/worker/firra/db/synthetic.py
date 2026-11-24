"""Sets up a workspace for the synthetic dataset: clients, domains, services, connections
(one mailbox per firm person, as in the user-connect mode, plus a calendar).

Only for synthetic data (local dev, evals, demos). Every statement is scoped by workspace_id.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from psycopg import Connection
from psycopg.rows import DictRow
from psycopg_pool import ConnectionPool

from firra.db.types import WorkspaceId
from firra.synthetic.spec import Spec

_CREATE_WORKSPACE = """
insert into public.workspace (id, name) values (%(workspace_id)s, %(name)s)
"""
_HAS_CLIENTS = """
select exists (select 1 from public.client where workspace_id = %(workspace_id)s) as has_clients
"""
_CLIENT = """
insert into public.client (workspace_id, name, status, policy, mrr, currency, contract_start,
                           renewal_date, notice_period_days, churned_at, service_tags)
values (%(workspace_id)s, %(name)s, %(status)s, %(policy)s, %(mrr)s, 'USD', %(contract_start)s,
        %(renewal_date)s, %(notice_period_days)s, %(churned_at)s, %(service_tags)s)
returning id
"""
_DOMAIN = """
insert into public.client_domain (workspace_id, client_id, domain)
values (%(workspace_id)s, %(client_id)s, %(domain)s)
"""
_SERVICE = """
insert into public.workspace_service (workspace_id, name, description, keywords)
values (%(workspace_id)s, %(name)s, %(description)s, %(keywords)s)
"""
_CONNECTION = """
insert into public.connection (workspace_id, provider, mode, account_label, external_account_id)
values (%(workspace_id)s, %(provider)s, %(mode)s, %(label)s, %(external)s)
returning id
"""
_REVOKE = """
update public.connection set status = 'revoked', last_synced_at = %(at)s
 where workspace_id = %(workspace_id)s and id = %(connection_id)s
"""


class WorkspaceNotEmptyError(Exception):
    """The synthetic dataset is loaded only into a workspace without clients."""


@dataclass(frozen=True, slots=True)
class SyntheticWorkspace:
    workspace_id: WorkspaceId
    clients: dict[str, UUID]
    # Firm person key -> that person's mailbox connection.
    mailboxes: dict[str, UUID]
    calendar_connection_id: UUID


def create_workspace(
    pool: ConnectionPool[Connection[DictRow]], *, workspace_id: WorkspaceId, name: str
) -> None:
    with pool.connection() as conn:
        conn.execute(_CREATE_WORKSPACE, {"workspace_id": workspace_id, "name": name})


def set_up(
    pool: ConnectionPool[Connection[DictRow]], *, workspace_id: WorkspaceId, spec: Spec
) -> SyntheticWorkspace:
    """Creates the spec's clients, services, one mailbox per firm person and a calendar."""
    base = {"workspace_id": workspace_id}
    founder = f"{spec.firm.people[0].key}@{spec.firm.domain}"
    with pool.connection() as conn:
        row = conn.execute(_HAS_CLIENTS, base).fetchone()
        if row is not None and row["has_clients"]:
            raise WorkspaceNotEmptyError(str(workspace_id))
        catalog = {service.key: service.name for service in spec.firm.services}
        clients: dict[str, UUID] = {}
        for client in spec.clients:
            created = conn.execute(
                _CLIENT,
                {
                    **base,
                    "name": client.name,
                    "status": client.status,
                    "policy": client.policy,
                    "mrr": client.mrr,
                    "contract_start": client.contract_start,
                    "renewal_date": client.renewal_date,
                    "notice_period_days": client.notice_period_days,
                    "churned_at": client.churned_at,
                    # A firm tags its clients with names from its service catalog.
                    "service_tags": [catalog[key] for key in client.services],
                },
            ).fetchone()
            if created is None:  # pragma: no cover - returning id
                raise RuntimeError("client insert returned no id")
            clients[client.key] = UUID(str(created["id"]))
            conn.execute(_DOMAIN, {**base, "client_id": created["id"], "domain": client.domain})
        for service in spec.firm.services:
            conn.execute(
                _SERVICE,
                {
                    **base,
                    "name": service.name,
                    "description": service.description,
                    "keywords": service.keywords,
                },
            )
        mailboxes: dict[str, UUID] = {}
        for person in spec.firm.people:
            mail = conn.execute(
                _CONNECTION,
                {
                    **base,
                    "provider": "email_upload",
                    "mode": "upload",
                    "label": f"{person.key}@{spec.firm.domain}",
                    "external": None,
                },
            ).fetchone()
            if mail is None:  # pragma: no cover - returning id
                raise RuntimeError("connection insert returned no id")
            mailboxes[person.key] = UUID(str(mail["id"]))
        # A user-mode calendar without a token: never synced, but its address marks the firm domain.
        calendar = conn.execute(
            _CONNECTION,
            {
                **base,
                "provider": "google_calendar",
                "mode": "user",
                "label": founder,
                "external": founder,
            },
        ).fetchone()
        if calendar is None:  # pragma: no cover - returning id
            raise RuntimeError("connection insert returned no id")
    return SyntheticWorkspace(
        workspace_id=workspace_id,
        clients=clients,
        mailboxes=mailboxes,
        calendar_connection_id=UUID(str(calendar["id"])),
    )


def revoke_mailbox(
    pool: ConnectionPool[Connection[DictRow]],
    *,
    workspace_id: WorkspaceId,
    connection_id: UUID,
    at: datetime,
) -> None:
    """Marks a mailbox revoked; ``last_synced_at`` is where its coverage ends (p2b D4)."""
    with pool.connection() as conn:
        conn.execute(
            _REVOKE, {"workspace_id": workspace_id, "connection_id": connection_id, "at": at}
        )
