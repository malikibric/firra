"""Provider failures and canonical hash checks without a database or paid APIs."""

import hashlib
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from firra.connectors.google.gmail import GmailClient
from firra.db.vault import VaultCandidate
from firra.http import ProviderError
from firra.tasks.handlers.vault import COUNTERS, _body
from firra.tasks.queue import QUEUE_FOR_TASK
from firra.tasks.registry import REGISTRY
from firra_contracts import Message
from firra_contracts.generated.tasks.vault_rehydrate import VaultRehydratePayload
from tests.fake_google import FakeGoogle
from tests.mail import email_bytes


def test_known_body_uses_the_ingest_hash_and_cleaner() -> None:
    fake = FakeGoogle()
    fake.add(
        "known",
        email_bytes(
            sender="ceo@acme.com",
            to=["ana@northbeam.agency"],
            body="Hello, here is the update.\n\n> quoted old reply",
        ),
    )
    candidate = VaultCandidate(
        uuid4(), "known", datetime.now(UTC), hashlib.sha256(b"Hello, here is the update.").digest()
    )
    progress = dict.fromkeys(COUNTERS, 0)
    with fake.client() as http:
        assert (
            _body(GmailClient(http, "access-ok").message, candidate, progress)
            == "Hello, here is the update."
        )
    assert progress == dict.fromkeys(COUNTERS, 0)


@pytest.mark.parametrize("status", [404, 401, 429, 503])
def test_only_missing_provider_errors_are_consumed(status: int) -> None:
    candidate = VaultCandidate(uuid4(), "known", datetime.now(UTC), None)
    progress = dict.fromkeys(COUNTERS, 0)

    def fetch(external_id: str) -> Message | None:
        raise ProviderError(status)

    if status == 404:
        assert _body(fetch, candidate, progress) is None
        assert progress["missing"] == 1
    else:
        with pytest.raises(ProviderError) as error:
            _body(fetch, candidate, progress)
        assert error.value.status == status and progress["missing"] == 0


def test_worker_contract_and_sync_queue() -> None:
    assert REGISTRY["vault.rehydrate"].payload_model is VaultRehydratePayload
    assert QUEUE_FOR_TASK["vault.rehydrate"] == "sync"
    with pytest.raises(ValidationError):
        VaultRehydratePayload.model_validate({"connection_id": str(uuid4()), "user_id": "invalid"})
