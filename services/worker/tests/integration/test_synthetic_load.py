"""The synthetic corpus loads through the real ingest path (fake bodies, no model calls)."""

import tempfile
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool

from firra.db import pool as db_pool
from firra.db.synthetic import WorkspaceNotEmptyError, create_workspace
from firra.db.types import WorkspaceId
from firra.llm.models import ModelSet
from firra.llm.prompts import load_prompt
from firra.llm.service import Llm
from firra.storage.raw import LocalRawStore
from firra.synthetic.__main__ import write_corpus
from firra.synthetic.facts import ingested
from firra.synthetic.generate import PROMPT_VERSION, CappedUsage, generate_corpus
from firra.synthetic.load import load_corpus, read_events, read_messages
from firra.synthetic.plan import build_plan
from firra.synthetic.spec import load_spec
from tests.conftest import local_settings
from tests.fake_synthdata import FakeWriter

pytestmark = pytest.mark.integration
Db = psycopg.Connection[dict[str, Any]]
MODELS = ModelSet(location="eu", extract=None, synth=None, embed=None, synthdata="gemini-3.8-flash")


@pytest.fixture
def db() -> Iterator[Db]:
    with psycopg.connect(
        local_settings().database_url.get_secret_value(), row_factory=dict_row, autocommit=True
    ) as conn:
        yield conn


@pytest.fixture
def pool() -> Iterator[ConnectionPool[psycopg.Connection[DictRow]]]:
    pool = db_pool.create_pool(local_settings())
    pool.open()
    try:
        yield pool
    finally:
        pool.close()


def test_corpus_loads_through_ingest(
    db: Db, pool: ConnectionPool[psycopg.Connection[DictRow]]
) -> None:
    spec = load_spec()
    plan = build_plan(spec)
    llm = Llm(backend=FakeWriter(), models=MODELS, usage=CappedUsage(cap_usd=Decimal(5)))
    corpus = generate_corpus(spec, plan, llm, load_prompt(PROMPT_VERSION))
    workspace_id = WorkspaceId(uuid4())
    create_workspace(pool, workspace_id=workspace_id, name="Northbeam synthetic test")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            corpus_dir = Path(tmp) / "corpus"
            write_corpus(corpus, {"test": True}, corpus_dir)
            messages, events = read_messages(corpus_dir), read_events(corpus_dir)
            assert len(messages) == 700
            raw = LocalRawStore(Path(tmp) / "raw")
            result = load_corpus(
                pool, raw, spec, workspace_id=workspace_id, messages=messages, events=events
            )
            assert result["clients"] == 15
            # Mail only the revoked mailbox would have received never arrives (coverage control).
            lost = sum(1 for t in plan.threads for m in t.messages if not ingested(spec, m))
            assert lost > 0 and result["not_ingested"] == lost
            assert result["messages"]["kept"] == 700 - lost
            assert result["messages"]["dropped"] == {}
            assert result["events"]["kept"] == len(plan.meetings)

            with pytest.raises(WorkspaceNotEmptyError):
                load_corpus(pool, raw, spec, workspace_id=workspace_id, messages=[], events=[])

        rows = db.execute(
            "select c.name, r.policy_at_ingest::text as policy, r.processing_state::text as state, "
            "r.direction::text as direction, r.body_clean, r.external_id "
            "from public.source_record r join public.client c on c.id = r.client_id "
            "where r.workspace_id = %s and r.kind = 'email'",
            (workspace_id,),
        ).fetchall()
        assert len(rows) == 700 - lost
        meridian = [r for r in rows if r["name"] == "Meridian Payroll"]
        assert len(meridian) == 50
        assert all(r["body_clean"] is None and r["state"] == "not_applicable" for r in meridian)
        others = [r for r in rows if r["name"] != "Meridian Payroll"]
        assert all(r["state"] == "pending" for r in others)
        assert {r["direction"] for r in others} == {"in", "out"}, "firm domain is recognized"

        # Planted text survives cleaning; decoration (signatures, quoted replies) does not.
        escalation = next(
            r for r in rows if r["external_id"].startswith("<brightpath_delivery_risk-3.1@")
        )
        assert "recovery plan by September 25." in escalation["body_clean"]
        assert "wrote:" not in escalation["body_clean"]
        assert "Founder and CEO" not in escalation["body_clean"]

        services = db.execute(
            "select count(*) as n from public.workspace_service where workspace_id = %s",
            (workspace_id,),
        ).fetchone()
        assert services is not None and services["n"] == 6
        churned = db.execute(
            "select count(*) as n from public.client where workspace_id = %s "
            "and status = 'churned' and churned_at is not null",
            (workspace_id,),
        ).fetchone()
        assert churned is not None and churned["n"] == 3
        mailboxes = db.execute(
            "select account_label, status::text as status, last_synced_at from public.connection "
            "where workspace_id = %s and provider = 'email_upload' order by account_label",
            (workspace_id,),
        ).fetchall()
        assert len(mailboxes) == len(spec.firm.people)
        revoked = [m for m in mailboxes if m["status"] == "revoked"]
        assert [m["account_label"] for m in revoked] == ["gus.meyer@northbeam.example"]
        assert revoked[0]["last_synced_at"].date().isoformat() == "2026-09-05"
    finally:
        db.execute("delete from public.workspace where id = %s", (workspace_id,))
