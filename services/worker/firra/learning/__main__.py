"""Local database only: python -m firra.learning report --workspace UUID [--json]."""

import argparse
import json
import os
import sys
from datetime import UTC, date, datetime
from uuid import UUID

from firra.db.learning import LearningRepo
from firra.db.locality import is_local_database
from firra.db.pool import create_pool_for
from firra.db.types import WorkspaceId
from firra.learning.config import load_config
from firra.learning.metrics import episodes, lead_time, precision, usefulness
from firra.learning.report import render_markdown, report_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m firra.learning")
    commands = parser.add_subparsers(dest="command", required=True)
    report = commands.add_parser("report")
    report.add_argument("--workspace", type=UUID, required=True)
    report.add_argument("--as-of", type=date.fromisoformat)
    report.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    url = os.environ.get("FIRRA_DATABASE_URL")
    if not url:
        sys.stderr.write("FIRRA_DATABASE_URL is required\n")
        return 2
    if not is_local_database(url):
        sys.stderr.write("learning from the CLI requires a local Postgres URL\n")
        return 2
    config = load_config()
    as_of = args.as_of or datetime.now(UTC).date()
    workspace_id = WorkspaceId(args.workspace)
    with create_pool_for(url) as pool:
        repo = LearningRepo(pool)
        feedback = repo.feedback(workspace_id=workspace_id, as_of=as_of)
        alerts = episodes(repo.assessments(workspace_id=workspace_id, as_of=as_of))
        outcomes = repo.outcomes(workspace_id=workspace_id, as_of=as_of)
    result = report_json(
        workspace_id,
        as_of,
        usefulness(feedback, config.min_rated),
        precision(alerts, outcomes, as_of, config.horizon_days, config.min_rated),
        lead_time(alerts, outcomes),
    )
    sys.stdout.write(json.dumps(result, indent=2) + "\n" if args.json else render_markdown(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
