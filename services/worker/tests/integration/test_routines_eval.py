"""Two free Northbeam passes through the real routine runner on local Postgres."""

import json
from dataclasses import replace
from datetime import date, timedelta
from functools import partial
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from firra.db.assessments import write_live
from firra.db.pool import create_pool_for
from firra.db.routines import RoutinesRepo
from firra.db.types import WorkspaceId
from firra.routines.config import load_config
from firra.routines.eval import (
    CardObs,
    ClientSeries,
    CommitmentInput,
    score_routines,
    score_suppression_probe,
)
from firra.routines.runner import run
from firra.synthetic.assessment import weekly_assessments
from firra.synthetic.plan import build_plan
from firra.synthetic.spec import load_spec
from firra.verify.quotes import BodyIndex, VerifiedQuote
from tests.conftest import local_settings

pytestmark = pytest.mark.integration


def insert(
    db: psycopg.Connection[dict[str, Any]], table: str, columns: str, values: tuple[Any, ...]
) -> UUID:
    query = sql.SQL("insert into {} ({}) values ({}) returning id").format(
        sql.Identifier(*table.split(".")),
        sql.SQL(", ").join(map(sql.Identifier, columns.split())),
        sql.SQL(", ").join(sql.Placeholder() for _ in values),
    )
    row = db.execute(query, values).fetchone()
    assert row
    return UUID(str(row["id"]))


def test_northbeam_routine_gates() -> None:
    spec = load_spec()
    corpus = build_plan(spec)
    series = {c.key: weekly_assessments(spec, corpus, c) for c in spec.clients}
    first = min(a.as_of for rows in series.values() for a in rows)
    last = max(a.as_of for rows in series.values() for a in rows)
    days = [
        first + timedelta(days=i)
        for i in range((last - first).days + 1)
        if (first + timedelta(days=i)).weekday() < 5
    ]
    # Copy the corpus series; no planner/spec or original pass is changed.
    probe_series = {}
    probes: dict[str, date] = {}
    for key, history in series.items():
        if not any(a.risk_state == "elevated" for a in history):
            continue
        down = last + timedelta(weeks=1 + 2 * len(probes))
        up = down + timedelta(weeks=2)
        probes[key] = up
        probe_series[key] = [
            *history,
            replace(
                history[-1],
                as_of=down,
                risk_state="watch",
                state_since=down,
                previous_risk_state=history[-1].risk_state,
            ),
            replace(
                history[-1],
                as_of=up,
                risk_state="elevated",
                state_since=up,
                previous_risk_state="watch",
            ),
        ]
    probe_last = max(probes.values())
    probe_days = days + [
        last + timedelta(days=i)
        for i in range(1, (probe_last - last).days + 1)
        if (last + timedelta(days=i)).weekday() < 5
    ]
    planted = [
        (t.client, m, item)
        for t in corpus.threads
        for m in t.messages
        for item in m.expect
        if item.kind == "commitment"
    ]
    url = local_settings().database_url.get_secret_value()
    results: dict[str, Any] = {}
    observations: dict[str, list[CardObs]] = {}
    with (
        psycopg.connect(url, row_factory=dict_row, autocommit=True) as db,
        create_pool_for(url) as pool,
    ):
        seed = partial(insert, db)
        cases = [("A", False, series, days), ("B", True, series, days)]
        # Isolate each probe client so a weekly budget demotion cannot steal its feedback prior.
        for key, history in probe_series.items():
            for side in ("A", "B"):
                cases.append((f"probe_{side}:{key}", side == "B", {key: history}, probe_days))
        for label, feedback, pass_series, run_days in cases:
            pass_clients = [c for c in spec.clients if c.key in pass_series]
            ws, user = uuid4(), uuid4()
            seed("public.workspace", "id name", (ws, "routine-eval"))
            seed("auth.users", "id", (user,))
            try:
                seed("public.membership", "workspace_id user_id role", (ws, user, "owner"))
                clients = {}
                inputs = []
                commitments: dict[str, list[CommitmentInput]] = {c.key: [] for c in pass_clients}
                for c in pass_clients:
                    cid = seed(
                        "public.client",
                        "workspace_id name status policy renewal_date notice_period_days",
                        (ws, c.key, "active", c.policy, c.renewal_date, c.notice_period_days),
                    )
                    clients[c.key] = cid
                    with db.transaction():
                        for assessment in pass_series[c.key]:
                            # No assessment evidence links: their real path is covered by T43d.
                            assessment = replace(
                                assessment,
                                drivers=tuple(
                                    replace(d, evidence_ids=()) for d in assessment.drivers
                                ),
                            )
                            write_live(
                                db,
                                {"workspace_id": ws, "client_id": cid},
                                assessment,
                                signal_ids={},
                                assessment_version="assessment.v1",
                                rule_version="signals.v1",
                            )
                connection = seed(
                    "public.connection",
                    "workspace_id provider mode account_label external_account_id",
                    (ws, "gmail", "user", "synthetic", "eval"),
                )
                for index, (key, message, item) in enumerate(planted):
                    if key not in clients:
                        continue
                    client = clients[key]
                    body = " ".join(message.include)
                    quote = BodyIndex(body).verify(item.quote)
                    assert isinstance(quote, VerifiedQuote)
                    with db.transaction():
                        record_values = (
                            ws,
                            connection,
                            "gmail",
                            str(index),
                            "email",
                            client,
                            "full",
                            message.at,
                            "out",
                            body,
                        )
                        record = seed(
                            "public.source_record",
                            "workspace_id connection_id provider external_id kind client_id "
                            "policy_at_ingest occurred_at direction body_clean",
                            record_values,
                        )
                        evidence = seed(
                            "public.evidence",
                            "workspace_id source_record_id client_id quote locator",
                            (ws, record, client, quote.quote, Jsonb(quote.locator)),
                        )
                        assert item.owner_side
                        commitment_values = (
                            ws,
                            client,
                            item.owner_side,
                            "Synthetic commitment",
                            item.due_date,
                            message.at,
                            str(index),
                        )
                        commitment = seed(
                            "public.commitment",
                            "workspace_id client_id owner_side text due_date stated_at dedupe_key",
                            commitment_values,
                        )
                        seed(
                            "public.commitment_evidence",
                            "workspace_id client_id commitment_id evidence_id",
                            (ws, client, commitment, evidence),
                        )
                    commitments[key].append(
                        CommitmentInput(
                            message.at.date(), item.due_date, item.owner_side, frozenset({evidence})
                        )
                    )
                for c in pass_clients:
                    maximum = next(
                        (
                            s.expect.max_risk
                            for s in spec.scenarios
                            if s.client == c.key and s.expect.max_risk
                        ),
                        None,
                    )
                    inputs.append(
                        ClientSeries(
                            c.key,
                            pass_series[c.key],
                            c.policy,
                            c.renewal_date,
                            c.notice_period_days,
                            c.churned_at,
                            max_risk=maximum,
                            commitments=commitments[c.key],
                        )
                    )
                repo = RoutinesRepo(pool)
                repo.ensure_routines(workspace_id=WorkspaceId(ws))
                routines = {
                    r["kind"]: r["id"]
                    for r in db.execute(
                        "select id, kind::text from public.routine where workspace_id = %s", (ws,)
                    ).fetchall()
                }
                rated = set()
                for day in run_days:
                    for kind in (
                        ("morning_scan", "commitment_watch")
                        if planted and not label.startswith("probe_")
                        else ("morning_scan",)
                    ):
                        run(
                            repo,
                            workspace_id=WorkspaceId(ws),
                            routine_id=routines[kind],
                            scheduled_for=day,
                        )
                    if feedback:
                        for card in db.execute(
                            "select id, client_id from public.decision_card where workspace_id = "
                            "%s and surfaced_on = %s and importance = 'important' "
                            "and (%s or reason_code = 'risk_rose_elevated') order by "
                            "priority, id",
                            (ws, day, not label.startswith("probe_")),
                        ).fetchall():
                            if card["client_id"] not in rated:
                                seed(
                                    "public.decision_feedback",
                                    "workspace_id client_id decision_card_id user_id verdict",
                                    (ws, card["client_id"], card["id"], user, "not_now"),
                                )
                                rated.add(card["client_id"])
                rows = db.execute(
                    """select c.name as client, d.surfaced_on as day, r.kind::text as routine,
                    d.kind::text, d.reason_code, d.importance::text, d.inputs->>'risk_rank' as rank,
                    d.demoted_reason, d.facts, array(select e.evidence_id
                      from public.decision_evidence e where e.workspace_id = d.workspace_id
                      and e.decision_card_id = d.id) as evidence_ids,
                    exists(select 1 from public.decision_feedback f
                      where f.workspace_id = d.workspace_id and f.decision_card_id = d.id
                      and f.verdict = 'not_now') as not_now
                    from public.decision_card d join public.client c
                      on c.workspace_id = d.workspace_id and c.id = d.client_id
                    join public.routine_run rr
                      on rr.workspace_id = d.workspace_id and rr.id = d.routine_run_id
                    join public.routine r
                      on r.workspace_id = rr.workspace_id and r.id = rr.routine_id
                    where d.workspace_id = %s""",
                    (ws,),
                ).fetchall()
                observed = []
                for row in rows:
                    row["risk_rank"] = int(row.pop("rank"))
                    row["fact_bases"] = tuple(f["basis"] for f in row.pop("facts"))
                    row["evidence_ids"] = frozenset(row["evidence_ids"])
                    observed.append(CardObs(**row))
                observations[label] = observed
                if not label.startswith("probe_"):
                    results[label] = score_routines(
                        inputs, observed, run_days, config=load_config(), feedback=feedback
                    ).as_json()
            finally:
                # All rows are synthetic and belong to this pass only.
                db.execute("delete from public.workspace where id = %s", (ws,))
                db.execute("delete from auth.users where id = %s", (user,))
    probe_a = [
        c for label, cards in observations.items() if label.startswith("probe_A:") for c in cards
    ]
    probe_b = [
        c for label, cards in observations.items() if label.startswith("probe_B:") for c in cards
    ]
    # Verify the probe has a real matching feedback prior, rather than another reason's feedback.
    for key, day in probes.items():
        assert any(
            c.client == key
            and c.day < day
            and c.not_now
            and c.kind == "state_change"
            and c.reason_code == "risk_rose_elevated"
            and c.risk_rank >= 2
            for c in probe_b
        ), ("missing probe feedback prior", key)
    probe = score_suppression_probe(probes, probe_a, probe_b)
    results["B"]["metrics"].update(probe.metrics)
    results["B"]["gates"]["suppression"] &= probe.gates["suppression"]
    results["B"]["passed"] = all(results["B"]["gates"].values())
    if probe.findings:
        results["B"]["findings"].setdefault("suppression", []).extend(probe.findings["suppression"])
    print("ROUTINE_EVAL_JSON=" + json.dumps(results, sort_keys=True))  # noqa: T201 — acceptance output
    assert all(result["passed"] for result in results.values()), results
