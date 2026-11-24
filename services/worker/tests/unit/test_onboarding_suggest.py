"""Client suggestions from message headers: ranking, exclusions, and the handler on a fake Gmail."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from firra.connectors.google.suggest import suggest_domains
from firra.tasks.handlers.onboarding import handle_onboarding_suggest
from firra_contracts import Message, OnboardingSuggestPayload
from tests.fake_google import FakeGoogle
from tests.mail import email_bytes

OWN = "owner@northbeam.test"


def message(
    thread: str, sender: str, to: list[str], day: int = 1, name: str | None = None
) -> Message:
    return Message.model_validate(
        {
            "provider": "gmail",
            "external_id": f"{thread}-{sender}-{day}",
            "thread_key": thread,
            "occurred_at": datetime(2026, 9, day, tzinfo=UTC),
            "sender": {"email": sender, "name": name},
            "to": [{"email": a} for a in to],
            "cc": [],
            "bcc": [],
        }
    )


def test_ranks_by_threads_and_skips_free_mail_own_domain_and_automatic_senders() -> None:
    mails = [
        message("t1", "ana@acme.test", [OWN], 1, "Ana Acme"),
        message("t1", OWN, ["ana@acme.test"], 2),
        message("t2", "ana@acme.test", [OWN], 3, "Ana Acme"),
        message("t3", "bo@acme.test", [OWN], 4),
        message("t4", "cy@bravo.test", [OWN], 5),
        message("t5", "cy@bravo.test", [OWN], 6),
        message("t6", "friend@gmail.com", [OWN], 7),
        message("t7", "friend@gmail.com", [OWN], 8),
        message("t8", "noreply@tool.test", [OWN], 9),
        message("t9", "noreply@tool.test", [OWN], 10),
        message("t10", "once@single.test", [OWN], 11),
        message("t11", "colleague@northbeam.test", [OWN], 12),
        message("t12", "colleague@northbeam.test", [OWN], 13),
    ]
    found = suggest_domains(mails, own_addresses=[OWN])
    assert [(s.domain, s.thread_count) for s in found] == [("acme.test", 3), ("bravo.test", 2)]
    assert found[0].sample_names == ("Ana Acme",)
    assert found[0].message_count == 4


def test_handler_scans_headers_only_and_returns_domains() -> None:
    fake = FakeGoogle(page_size=3)
    for i in range(6):
        fake.add(
            f"m{i}",
            email_bytes(sender=f"x{i}@acme.test", to=[OWN], message_id=f"<m{i}@acme.test>"),
            thread_id=f"t{i}",
        )
    connection = SimpleNamespace(
        id=uuid4(), provider="gmail", backfill_months=3, account_label=OWN, status="active"
    )
    ctx: Any = SimpleNamespace(
        http=fake.client(),
        time_budget_seconds=30,
        google=SimpleNamespace(access_token=lambda c, scopes: "access-ok"),
        connections=SimpleNamespace(get=lambda **kw: connection),
        jobs=SimpleNamespace(update_progress=lambda **kw: progress.append(kw["progress"])),
    )
    progress: list[dict[str, int]] = []
    job: Any = SimpleNamespace(workspace_id=uuid4(), id=uuid4(), progress={})
    result = handle_onboarding_suggest(
        ctx, job, OnboardingSuggestPayload(connection_id=connection.id)
    )
    assert result["scanned"] == 6
    assert [(s["domain"], s["thread_count"]) for s in result["suggestions"]] == [("acme.test", 6)]
    assert set(fake.formats) == {"metadata"}  # bodies are never fetched
    assert progress and progress[-1] == {"scanned": 6}  # the onboarding screen can show progress
