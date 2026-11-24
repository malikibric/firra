"""Executor eval on recorded output (L3-06a): truth replay, corruption, states.

Loads the Northbeam corpus into the bundled database, runs PacketProcessor
with a ReplayExecutor that answers from truth.json (spec-enriched), then
checks quote validity, corruption rejection, and the downstream risk states
for umber, vantage, pinecrest and halden. No production code changes.
"""

import json
import os
import tempfile
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from firra.ai.processor import PacketProcessor
from firra.assessment.assess import assess
from firra.assessment.complaints import unresolved_complaints
from firra.assessment.config import load_assessment_config, load_plays_config
from firra.assessment.guard import coverage_guard, volume_rule_types
from firra.db.ai_processing import AiProcessingRepo
from firra.db.memory import MemoryRepo
from firra.db.pool import create_pool_for
from firra.db.signals import SignalRepo
from firra.db.synthetic import create_workspace
from firra.db.types import WorkspaceId
from firra.llm.prompts import load_prompt
from firra.memory.rules import load_config as load_memory_config
from firra.signals.config import load_config as load_signals_config
from firra.signals.engine import evaluate
from firra.storage.raw import LocalRawStore
from firra.synthetic.load import CORPUS_DIR, load_corpus, read_events, read_messages
from firra.synthetic.spec import SPEC_PATH, load_spec
from tests.integration.replay_executor import ReplayExecutor

pytestmark = pytest.mark.integration
PG_BIN = os.environ.get("FIRRA_LOCAL_PG_BIN")
TRUTH_PATH = (
    Path(__file__).resolve().parent.parent.parent / "synthetic" / "northbeam" / "truth.json"
)

EXPECTATIONS = {
    "umber": "elevated",
    "vantage": "watch",
    "pinecrest": "stable",
    "halden": "stable",
}


def _load_truth() -> Any:
    return json.loads(TRUTH_PATH.read_text())


@pytest.fixture(scope="module")
def worlds() -> Iterator[dict[str, Any]]:
    if not PG_BIN or not (Path(PG_BIN) / "initdb").exists():
        pytest.skip("FIRRA_LOCAL_PG_BIN is not set")
    from firra.local.db import migrate
    from firra.local.pg import PostgresSupervisor

    root = Path(tempfile.mkdtemp(prefix="firra-oracle-exec-eval-", dir="/tmp"))
    pg = PostgresSupervisor(Path(PG_BIN), root / "data", root / "run")
    conninfo = pg.ensure_ready()
    migrate(conninfo)
    spec = load_spec()
    truth = _load_truth()
    out: dict[str, Any] = {"spec": spec, "truth_json": truth, "conninfo": conninfo}
    for workspace_key, corrupt_every in (("truth", 0), ("corrupt", 10)):
        ws = WorkspaceId(uuid4())
        pool = create_pool_for(conninfo, min_size=1, max_size=4)
        pool.open()
        create_workspace(pool, workspace_id=ws, name=f"Northbeam eval {workspace_key}")
        raw = LocalRawStore(root / f"raw-{workspace_key}")
        result = load_corpus(
            pool, raw, spec, workspace_id=ws, messages=read_messages(), events=read_events()
        )
        assert result["clients"] == 15
        executor = ReplayExecutor(CORPUS_DIR, SPEC_PATH, TRUTH_PATH, corrupt_every=corrupt_every)
        processor = PacketProcessor(executor, AiProcessingRepo(pool), load_prompt("extract.v6"), ws)
        while True:
            pending = AiProcessingRepo(pool).pending(workspace_id=ws, limit=20)
            if not pending:
                break
            processor.process(pending)
        out[workspace_key] = {
            "ws": ws,
            "pool": pool,
            "stats": processor.stats,
            "executor": executor,
            "corpus_messages": len(read_messages()),
        }
    yield out
    for workspace_key in ("truth", "corrupt"):
        out[workspace_key]["pool"].close()
    pg.stop()


def _stored_quotes(pool: Any, ws: WorkspaceId) -> set[str]:
    with pool.connection() as conn:
        rows = conn.execute(
            "select e.quote from public.evidence e where e.workspace_id = %s", (ws,)
        ).fetchall()
        return {str(r["quote"]) for r in rows}


def test_truth_output_fully_accepted(worlds: dict[str, Any]) -> None:
    """Every truth quote verifies: rejected is 0 and all 55 quotes are stored."""
    entry, truth = worlds["truth"], worlds["truth_json"]
    assert dict(entry["stats"].dropped) == {}, f"dropped: {dict(entry['stats'].dropped)}"
    stored = _stored_quotes(entry["pool"], entry["ws"])
    missing = [item["quote"] for item in truth["items"] if item["quote"] not in stored]
    assert not missing, f"truth quotes never stored: {missing[:3]} ({len(missing)} total)"


def test_corrupted_quotes_dropped_never_stored(worlds: dict[str, Any]) -> None:
    """Every 10th corrupted quote is dropped with a reason and stored nowhere."""
    entry = worlds["corrupt"]
    dropped = dict(entry["stats"].dropped)
    assert sum(dropped.values()) > 0, "expected rejections for corrupted quotes"
    stored = _stored_quotes(entry["pool"], entry["ws"])
    assert not any("xyzzy" in quote for quote in stored)
    for quote in entry["executor"].corrupted:
        assert quote not in stored
    accepted = entry["stats"].items
    assert accepted == 55 - len(entry["executor"].corrupted)


def _assess_client(
    pool: Any, ws: WorkspaceId, truth: dict[str, Any], client_key: str, by: str | None
) -> str:
    name = truth["clients"][client_key]["name"]
    with pool.connection() as conn:
        row = conn.execute(
            "select id from public.client where workspace_id = %s and name = %s", (ws, name)
        ).fetchone()
        assert row is not None, f"client {name} not loaded"
        client_id: UUID = row["id"]
    as_of = date.fromisoformat(by) if by else date.fromisoformat(str(truth["as_of"]))
    assert MemoryRepo(pool).rebuild_client(
        workspace_id=ws, client_id=client_id, as_of=as_of, cfg=load_memory_config()
    )
    ref = datetime(as_of.year, as_of.month, as_of.day, 23, 59, tzinfo=UTC)
    facts = SignalRepo(pool).load_facts(workspace_id=ws, client_id=client_id, ref=ref)
    assert facts is not None, f"no facts for {client_key!r}"
    signals_cfg = load_signals_config()
    assessment_cfg = load_assessment_config()
    drafts, guard_caveats = coverage_guard(
        facts,
        evaluate(facts, signals_cfg).drafts,
        volume_rule_types(),
        tolerance=timedelta(days=assessment_cfg.coverage.tolerance_days),
    )
    result = assess(
        drafts,
        facts,
        None,
        assessment_cfg,
        load_plays_config(),
        volume_types=volume_rule_types(),
        unresolved_complaints=unresolved_complaints,
        guard_caveats=guard_caveats,
    )
    return str(result.risk_state)


def test_downstream_risk_states(worlds: dict[str, Any]) -> None:
    """umber/vantage reach their truth states; pinecrest/halden stay stable."""
    entry, truth = worlds["truth"], worlds["truth_json"]
    actual = {}
    for client_key in EXPECTATIONS:
        by = truth["clients"][client_key]["assessment"].get("by")
        actual[client_key] = _assess_client(entry["pool"], entry["ws"], truth, client_key, by)
    assert actual == EXPECTATIONS, f"risk states differ: {actual}"
