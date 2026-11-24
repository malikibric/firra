"""Minimization: decide, before anything is persisted, whether a record may be stored.

Rules (docs/architecture.md § Minimization filter, CLAUDE.md product rule 9):
1. If any participant belongs to an ``excluded`` client, the record is dropped entirely.
2. Otherwise it is kept only if at least one participant maps to a client (by email domain,
   including subdomains). Internal-only records are kept only when the workspace enables
   internal mentions and exactly one client is named in the subject or body.
3. With several clients involved, the most restrictive policy wins (``metadata_only`` beats
   ``full``) so one client's content is never stored under another client's policy.

Pure functions only: no I/O, no logging of content.
"""

import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

Policy = Literal["full", "metadata_only", "excluded"]
Direction = Literal["in", "out", "internal"]
DropReason = Literal["no_client", "excluded_client", "ambiguous_mention"]


@dataclass(frozen=True, slots=True)
class ClientRef:
    id: UUID
    name: str
    policy: Policy


@dataclass(frozen=True, slots=True)
class Directory:
    """What minimization needs to know about a workspace, loaded once per sync run."""

    client_domains: Mapping[str, ClientRef]
    firm_domains: frozenset[str]
    internal_mentions: bool = False


@dataclass(frozen=True, slots=True)
class Keep:
    client: ClientRef
    policy: Literal["full", "metadata_only"]
    direction: Direction


@dataclass(frozen=True, slots=True)
class Drop:
    reason: DropReason


Decision = Keep | Drop


def email_domain(email: str) -> str | None:
    local, sep, domain = email.strip().lower().rpartition("@")
    domain = domain.rstrip(".")
    if not sep or not local or "." not in domain:
        return None
    return domain


def _parent_domains(domain: str) -> Iterable[str]:
    labels = domain.split(".")
    for i in range(len(labels) - 1):
        yield ".".join(labels[i:])


def client_for(email: str, directory: Directory) -> ClientRef | None:
    domain = email_domain(email)
    if domain is None:
        return None
    for candidate in _parent_domains(domain):
        client = directory.client_domains.get(candidate)
        if client is not None:
            return client
    return None


def is_firm(email: str, directory: Directory) -> bool:
    domain = email_domain(email)
    return domain is not None and any(d in directory.firm_domains for d in _parent_domains(domain))


def _most_restrictive(clients: Iterable[ClientRef]) -> Literal["full", "metadata_only"]:
    return "metadata_only" if any(c.policy == "metadata_only" for c in clients) else "full"


def _mentioned_clients(text: str, directory: Directory) -> set[ClientRef]:
    lowered = text.lower()
    found: set[ClientRef] = set()
    for domain, client in directory.client_domains.items():
        if re.search(rf"(?<![\w.-]){re.escape(domain)}(?![\w-])", lowered):
            found.add(client)
    for client in set(directory.client_domains.values()):
        name = client.name.strip().lower()
        # Whole-name match: "Acme's" counts, "acme-corp" and "acmeish" do not.
        if len(name) >= 3 and re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", lowered):
            found.add(client)
    return found


def needs_content(sender: str, others: Iterable[str], directory: Directory) -> bool:
    """Whether a record might be kept, so its content is worth fetching from the provider.

    False means ``decide`` drops it whatever the content says: providers that can list
    participants first (Gmail headers) never download non-client mail.
    """
    recipients = list(others)
    if isinstance(decide(sender, recipients, directory), Keep):
        return True
    internal = all(is_firm(email, directory) for email in [sender, *recipients])
    return internal and directory.internal_mentions


def decide(
    sender: str,
    others: Iterable[str],
    directory: Directory,
    *,
    mention_text: str = "",
) -> Decision:
    """Decide for one record: ``sender`` is the author/organizer, ``others`` everyone else."""
    participants = [sender, *others]
    mapped = [client_for(email, directory) for email in participants]

    if any(client is not None and client.policy == "excluded" for client in mapped):
        return Drop("excluded_client")

    clients = [client for client in mapped if client is not None]
    if not clients:
        internal = all(is_firm(email, directory) for email in participants)
        if not (internal and directory.internal_mentions and mention_text):
            return Drop("no_client")
        mentioned = _mentioned_clients(mention_text, directory)
        if any(client.policy == "excluded" for client in mentioned):
            return Drop("excluded_client")
        if len(mentioned) != 1:
            return Drop("ambiguous_mention" if mentioned else "no_client")
        (client,) = mentioned
        return Keep(client=client, policy=_most_restrictive([client]), direction="internal")

    sender_client = mapped[0]
    if sender_client is not None:
        primary = sender_client
    else:
        counts = Counter(clients)
        top = max(counts.values())
        primary = min((c for c, n in counts.items() if n == top), key=lambda c: str(c.id))

    direction: Direction = "in" if not is_firm(sender, directory) else "out"
    return Keep(client=primary, policy=_most_restrictive(clients), direction=direction)
