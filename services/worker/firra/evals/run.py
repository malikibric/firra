"""Guarded local Northbeam pipeline run; model calls happen only in the worker."""

import json
import math
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx

from firra.assessment.runner import run_replay
from firra.db.evals import EvalRepo, EvalRunRepo, RunState
from firra.db.locality import is_local_database
from firra.db.pool import create_pool_for
from firra.db.replay import ReplayRepo
from firra.db.signals import SignalRepo
from firra.db.synthetic import create_workspace
from firra.db.types import WorkspaceId
from firra.evals.models import Truth
from firra.evals.replay_score import score_replay
from firra.evals.report import write_report
from firra.evals.score import score
from firra.storage.raw import LocalRawStore
from firra.synthetic.load import CORPUS_DIR, load_corpus, read_events, read_messages
from firra.synthetic.spec import load_spec
from firra.synthetic.truth import TRUTH_PATH
from firra.tasks.queue import LocalQueue

# One year, the corpus window: the older losses churned more than 180 days before as_of, and a
# churned client's replay stops at its churn date, so a shorter window would leave them unscored.
REPLAY_DAYS = 365
REPLAY_STEP_DAYS = 7
# A queued job nobody works on for this long is delivered again (see ``stalled_jobs``).
STALL_SECONDS = 180
# The cards stage must not use up the whole run; scoring goes on without it (the card gate reports).
CARDS_STAGE_SECONDS = 20 * 60


@dataclass(frozen=True)
class RunOptions:
    out: Path
    yes: bool = False
    budget_usd: Decimal = Decimal("1.75")
    timeout_min: float = 100
    worker_url: str = "http://127.0.0.1:8080"
    corpus_dir: Path = CORPUS_DIR


def wait_for_settle(
    settle: Callable[[], RunState],
    *,
    deadline: float,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[RunState, bool]:
    """Two consecutive quiet polls ten seconds apart cover delayed task continuations."""
    quiet = 0
    while True:
        state = settle()
        if state.failed:
            return state, False
        if clock() >= deadline:
            return state, True
        quiet = quiet + 1 if not state.active and not state.pending else 0
        if quiet == 2:
            return state, False
        sleep(min(10, max(0, deadline - clock())))


def outcome_code(state: RunState, timed_out: bool) -> int:
    return 3 if timed_out or state.failed or state.pending or state.active else 0


def run_evaluation(
    options: RunOptions,
    *,
    http: httpx.Client,
    settle: Callable[[EvalRunRepo], RunState] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    url = os.environ.get("FIRRA_DATABASE_URL", "")
    try:
        if not is_local_database(url):
            raise ValueError("eval run requires a local Postgres URL")
        if (
            not options.budget_usd.is_finite()
            or options.budget_usd <= 0
            or not math.isfinite(options.timeout_min)
            or options.timeout_min <= 0
        ):
            raise ValueError("budget and timeout must be positive")
        messages, events = read_messages(options.corpus_dir), read_events(options.corpus_dir)
        truth_data = json.loads(TRUTH_PATH.read_text())
        truth = Truth.model_validate(truth_data)
        as_of = date.fromisoformat(truth_data["as_of"])
        spec = load_spec()
        response = http.get(f"{options.worker_url.rstrip('/')}/health", timeout=10)
        response.raise_for_status()
        if response.json() != {"status": "ok"}:
            raise ValueError("worker health check failed")
        secret = os.environ.get("FIRRA_LOCAL_TASK_SECRET", "")
        if options.yes and len(secret) < 32:
            raise ValueError("FIRRA_LOCAL_TASK_SECRET must have at least 32 characters")
    except (ValueError, OSError, httpx.HTTPError, KeyError):
        sys.stderr.write(
            "eval run guard failed: local database, corpus, health and configuration required\n"
        )
        return 2
    if not options.yes:
        sys.stdout.write(
            f"Northbeam: {len(messages)} messages, {len(events)} events; about 1\u20132 USD\n"
        )
        return 0

    started = clock()
    deadline = started + options.timeout_min * 60
    workspace_id = WorkspaceId(uuid4())
    state: RunState | None = None
    timed_out = False
    error_class: str | None = None
    code = 3
    cards_stage = "not_reached"
    try:
        with create_pool_for(url) as pool:
            repo = EvalRunRepo(pool, workspace_id)
            create_workspace(
                pool,
                workspace_id=workspace_id,
                name=f"Northbeam eval {datetime.now(UTC):%Y-%m-%dT%H:%M:%SZ}",
            )
            repo.set_budget(options.budget_usd)
            raw = LocalRawStore(Path(os.environ.get("FIRRA_LOCAL_RAW_DIR") or options.out / "raw"))
            load_corpus(
                pool, raw, spec, workspace_id=workspace_id, messages=messages, events=events
            )
            queue = LocalQueue(options.worker_url.rstrip("/"), secret, http)
            base_poll: Callable[[], RunState] = (lambda: settle(repo)) if settle else repo.state

            def poll() -> RunState:
                for envelope in repo.stalled_jobs(older_than_seconds=STALL_SECONDS):
                    queue.enqueue(envelope)
                return base_poll()

            queue.enqueue(repo.request_job("process.records", {"trigger": "sweep"}))
            state, timed_out = wait_for_settle(poll, deadline=deadline, clock=clock, sleep=sleep)
            code = outcome_code(state, timed_out)
            if code == 0:
                queue.enqueue(
                    repo.request_job(
                        "memory.rebuild", {"trigger": "sweep", "as_of": as_of.isoformat()}
                    )
                )
                state, timed_out = wait_for_settle(
                    poll, deadline=deadline, clock=clock, sleep=sleep
                )
                code = outcome_code(state, timed_out)
            if code == 0:
                # Cards of the week that contains as_of (Monday), worded by the synth model inside
                # the same budget cap; the stage must settle before anything is scored.
                repo.discard_live_assessments_after(as_of)
                week = as_of - timedelta(days=as_of.weekday())
                queue.enqueue(
                    repo.request_job("synth.cards", {"trigger": "manual", "week": week.isoformat()})
                )
                state, timed_out = wait_for_settle(
                    poll,
                    deadline=min(deadline, clock() + CARDS_STAGE_SECONDS),
                    clock=clock,
                    sleep=sleep,
                )
                # A cards stage that failed or timed out must not cost the whole scoring run: the
                # card gate fails the verdict when plays are expected and no card exists.
                cards_stage = (
                    "ok"
                    if not (state.failed or timed_out)
                    else ("timed_out" if timed_out else "failed")
                )
                timed_out = False
            if code == 0:
                replay_repo = ReplayRepo(pool)
                replay_run = replay_repo.create_run(
                    workspace_id=workspace_id,
                    window_start=as_of - timedelta(days=REPLAY_DAYS),
                    window_end=as_of,
                    step_days=REPLAY_STEP_DAYS,
                )
                replayed = run_replay(
                    SignalRepo(pool), replay_repo, workspace_id=workspace_id, run_id=replay_run
                )
                eval_repo = EvalRepo(pool, workspace_id)
                client_keys = {c.name: key for key, c in truth.clients.items()}
                inputs = eval_repo.collect().model_copy(
                    update={
                        "signals": eval_repo.collect_signals(replay_run, client_keys),
                        "needs": eval_repo.collect_needs(
                            client_keys, {s.name: s.key for s in spec.firm.services}
                        ),
                        "cards": eval_repo.collect_cards(client_keys),
                    }
                )
                corpus = {m.external_id: m.body_text for m in messages if m.body_text is not None}
                result = score(inputs, truth, corpus)
                names = eval_repo.client_names()
                replay = score_replay(
                    eval_repo.collect_replay(replay_run),
                    truth,
                    step_days=REPLAY_STEP_DAYS,
                    lead_days={
                        client_keys[names[c.client_id]]: c.lead_days
                        for c in replayed.summary.clients
                        if c.status == "churned" and names.get(c.client_id) in client_keys
                    },
                )
                write_report(result, options.out, replay)
                code = 0 if result.passed and replay.passed else 1
    except Exception as exc:
        # Exception text can contain connection strings or model data; retain only its class.
        error_class = type(exc).__name__
        code = 3
    options.out.mkdir(parents=True, exist_ok=True)
    (options.out / "run.json").write_text(
        json.dumps(
            {
                "workspace_id": str(workspace_id),
                "duration_seconds": round(clock() - started, 2),
                "exit_code": code,
                "timed_out": timed_out,
                "error_class": error_class,
                "jobs": state.jobs if state else [],
                "cards_stage": cards_stage,
                "failed_jobs": state.failed if state else [],
                "pending": state.pending if state else None,
                "llm_usage": state.usage if state else {},
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return code
