"""Local-only CLI: python -m firra.evals score --workspace UUID --out DIR."""

import argparse
import os
import sys
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from firra.db.evals import EvalRepo
from firra.db.locality import is_local_database
from firra.db.pool import create_pool_for
from firra.db.types import WorkspaceId
from firra.evals.lab_cli import add_arguments as add_lab_arguments
from firra.evals.lab_cli import run_command as run_lab_command
from firra.evals.models import Truth
from firra.evals.report import write_report
from firra.evals.run import RunOptions, run_evaluation
from firra.evals.score import score
from firra.synthetic.load import CORPUS_DIR, MESSAGES_FILE, read_messages
from firra.synthetic.truth import TRUTH_PATH
from firra.tasks.queue import local_delivery_client


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m firra.evals")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("score", help="score an already processed local workspace")
    run.add_argument("--workspace", type=UUID, required=True)
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--corpus-dir", type=Path, default=CORPUS_DIR)
    pipeline = commands.add_parser("run", help="run and score the local Northbeam pipeline")
    pipeline.add_argument("--out", type=Path, required=True)
    pipeline.add_argument("--yes", action="store_true")
    pipeline.add_argument("--budget-usd", type=Decimal)
    pipeline.add_argument("--timeout-min", type=float, default=100)
    pipeline.add_argument("--worker-url", default="http://127.0.0.1:8080")
    pipeline.add_argument("--corpus-dir", type=Path, default=CORPUS_DIR)
    add_lab_arguments(commands)
    args = parser.parse_args(argv)
    if args.command == "prompt-lab":
        return run_lab_command(args)
    if args.command == "run":
        try:
            budget = (
                args.budget_usd
                if args.budget_usd is not None
                else Decimal(os.environ.get("FIRRA_EVAL_BUDGET_USD", "1.75"))
            )
        except ArithmeticError:
            sys.stderr.write("invalid eval budget\n")
            return 2
        with local_delivery_client() as http:
            return run_evaluation(
                RunOptions(
                    out=args.out,
                    yes=args.yes,
                    budget_usd=budget,
                    timeout_min=args.timeout_min,
                    worker_url=args.worker_url,
                    corpus_dir=args.corpus_dir,
                ),
                http=http,
            )
    url = os.environ.get("FIRRA_DATABASE_URL")
    if not url:
        sys.stderr.write("FIRRA_DATABASE_URL is required\n")
        return 2
    if not is_local_database(url):
        sys.stderr.write("score requires a local Postgres URL\n")
        return 2
    corpus_file = args.corpus_dir / MESSAGES_FILE
    if not corpus_file.is_file():
        sys.stderr.write(f"Northbeam corpus file is missing: {corpus_file}\n")
        return 2
    corpus = {
        message.external_id: message.body_text
        for message in read_messages(args.corpus_dir)
        if message.body_text is not None
    }
    truth = Truth.model_validate_json(TRUTH_PATH.read_text())
    with create_pool_for(url) as pool:
        inputs = EvalRepo(pool, WorkspaceId(args.workspace)).collect()
    result = score(inputs, truth, corpus)
    write_report(result, args.out)
    sys.stdout.write(f"{'PASS' if result.passed else 'FAIL'}: {args.out / 'eval.md'}\n")
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
