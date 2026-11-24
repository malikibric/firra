"""Local-only CLI, no model calls.

python -m firra.assessment replay --workspace UUID --from DATE --to DATE [--step 7]
    creates a replay run, runs it against a local database and prints the summary.
python -m firra.assessment report --workspace UUID --run UUID --out DIR
    writes one Markdown Churn Replay report per client and an overview (`index.md`).
python -m firra.assessment narrate --workspace UUID --run UUID
    backfills the stored narrative rows of a succeeded run without model calls.
python -m firra.assessment estimate --workspace UUID [--months 12]
    counts full-policy emails and estimates extraction cost without reading content.
python -m firra.assessment pilot-check --workspace UUID --run UUID
    prints a finished run's totals, miss reasons and caveat counts without content.
"""

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path
from uuid import UUID

from firra.assessment.pilot import render_estimate, render_pilot_check
from firra.assessment.runner import run_replay
from firra.assessment.write_reports import write_reports
from firra.db.estimate import EstimateRepo
from firra.db.locality import is_local_database
from firra.db.pool import create_pool_for
from firra.db.replay import ReplayRepo, ReportRepo
from firra.db.signals import SignalRepo
from firra.db.types import WorkspaceId

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _months(value: str) -> int:
    months = int(value)
    if months < 1:
        raise argparse.ArgumentTypeError("months must be positive")
    return months


def _estimate(url: str, workspace_id: WorkspaceId, months: int) -> int:
    with create_pool_for(url) as pool:
        rows = EstimateRepo(pool).counts(workspace_id=workspace_id, months=months)
    sys.stdout.write(render_estimate(rows, months=months))
    return 0


def _pilot_check(url: str, workspace_id: WorkspaceId, run_id: UUID) -> int:
    with create_pool_for(url) as pool:
        repo = ReportRepo(pool)
        run = repo.pilot_run(workspace_id=workspace_id, run_id=run_id)
        if run is None or run["status"] != "succeeded":
            sys.stderr.write("replay run not found or not finished\n")
            return 2
        clients = repo.pilot_clients(workspace_id=workspace_id, run_id=run_id)
    sys.stdout.write(render_pilot_check(run["totals"] or {}, clients))
    return 0


def _report(url: str, workspace_id: WorkspaceId, run_id: UUID, out: Path) -> int:
    with create_pool_for(url) as pool:
        written = write_reports(pool, workspace_id=workspace_id, run_id=run_id, out_dir=out)
    if written is None:
        sys.stderr.write("replay run not found or not finished\n")
        return 2
    sys.stdout.write(f"{written} files in {out}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m firra.assessment")
    commands = parser.add_subparsers(dest="command", required=True)
    replay = commands.add_parser("replay", help="replay a local workspace week by week")
    replay.add_argument("--workspace", type=UUID, required=True)
    replay.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
    replay.add_argument("--to", dest="end", type=date.fromisoformat, required=True)
    replay.add_argument("--step", type=int, default=7)
    report = commands.add_parser("report", help="write Churn Replay reports for a finished run")
    report.add_argument("--workspace", type=UUID, required=True)
    report.add_argument("--run", type=UUID, required=True)
    report.add_argument("--out", type=Path, required=True)
    narrate = commands.add_parser("narrate", help="backfill a finished local run")
    narrate.add_argument("--workspace", type=UUID, required=True)
    narrate.add_argument("--run", type=UUID, required=True)
    estimate = commands.add_parser(
        "estimate", help="count emails and estimate pilot extraction cost"
    )
    estimate.add_argument("--workspace", type=UUID, required=True)
    estimate.add_argument("--months", type=_months, default=12)
    check = commands.add_parser("pilot-check", help="check replay health without reading content")
    check.add_argument("--workspace", type=UUID, required=True)
    check.add_argument("--run", type=UUID, required=True)
    args = parser.parse_args(argv)
    url = os.environ.get("FIRRA_DATABASE_URL")
    if not url:
        sys.stderr.write("FIRRA_DATABASE_URL is required\n")
        return 2
    if not is_local_database(url):
        sys.stderr.write("replay from the CLI requires a local Postgres URL\n")
        return 2
    workspace_id = WorkspaceId(args.workspace)
    if args.command == "narrate":
        with create_pool_for(url) as pool:
            finished = ReplayRepo(pool).narrate(workspace_id=workspace_id, run_id=args.run)
        if not finished:
            sys.stderr.write("replay run not found or not finished\n")
            return 2
        return 0
    if args.command == "report":
        return _report(url, workspace_id, args.run, args.out)
    if args.command == "estimate":
        return _estimate(url, workspace_id, args.months)
    if args.command == "pilot-check":
        return _pilot_check(url, workspace_id, args.run)
    with create_pool_for(url) as pool:
        repo = ReplayRepo(pool)
        run_id = repo.create_run(
            workspace_id=workspace_id,
            window_start=args.start,
            window_end=args.end,
            step_days=args.step,
        )
        result = run_replay(SignalRepo(pool), repo, workspace_id=workspace_id, run_id=run_id)
    sys.stdout.write(
        json.dumps({"replay_run_id": str(run_id), **result.summary.as_json()}, indent=2) + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
