"""Client suggestions for onboarding: which outside domains does the owner talk to most?

Pure and deterministic: input is message headers (never bodies), output is a ranked list of
domains with thread counts, so the owner can confirm clients before anything is stored. Free-mail
providers, the owner's own domain and automatic senders are never suggested.
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from firra_contracts import Message

FREE_MAIL = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "outlook.com",
        "hotmail.com",
        "live.com",
        "msn.com",
        "yahoo.com",
        "ymail.com",
        "icloud.com",
        "me.com",
        "mac.com",
        "aol.com",
        "proton.me",
        "protonmail.com",
        "gmx.com",
        "gmx.de",
        "gmx.net",
        "web.de",
        "mail.com",
        "t-online.de",
        "orange.fr",
        "free.fr",
        "libero.it",
        "btinternet.com",
        "sky.com",
        "bluewin.ch",
    }
)
AUTOMATIC_LOCAL_PARTS = ("noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon")


@dataclass(frozen=True, slots=True)
class Suggestion:
    domain: str
    thread_count: int
    message_count: int
    last_seen: datetime
    sample_names: tuple[str, ...]


def _domain(address: str) -> str | None:
    local, _, domain = address.strip().lower().rpartition("@")
    if not local or not domain or local.startswith(AUTOMATIC_LOCAL_PARTS):
        return None
    return domain


def suggest_domains(
    messages: Iterable[Message],
    *,
    own_addresses: Iterable[str],
    min_threads: int = 2,
    limit: int = 30,
) -> list[Suggestion]:
    own_domains = {d for a in own_addresses if (d := _domain(a)) is not None}
    threads: dict[str, set[str]] = defaultdict(set)
    counts: dict[str, int] = defaultdict(int)
    seen: dict[str, datetime] = {}
    names: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for message in messages:
        people = [message.sender, *message.to, *message.cc]
        for domain in {d for p in people if (d := _domain(p.email)) is not None}:
            if domain in FREE_MAIL or domain in own_domains:
                continue
            threads[domain].add(message.thread_key or message.external_id)
            counts[domain] += 1
            seen[domain] = max(seen.get(domain, message.occurred_at), message.occurred_at)
        for person in people:
            known = _domain(person.email)
            if known in threads and person.name:
                names[known][person.name.strip()] += 1
    ranked = sorted(
        (d for d, t in threads.items() if len(t) >= min_threads),
        key=lambda d: (-len(threads[d]), -counts[d], d),
    )
    return [
        Suggestion(
            domain=d,
            thread_count=len(threads[d]),
            message_count=counts[d],
            last_seen=seen[d],
            sample_names=tuple(
                n for n, _ in sorted(names[d].items(), key=lambda kv: (-kv[1], kv[0]))[:3]
            ),
        )
        for d in ranked[:limit]
    ]
