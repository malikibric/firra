"""CLI for the synthetic dataset: python -m firra.synthetic <plan|truth|generate|load>.

plan, truth and load never call a model. generate is the only command that costs money: it
refuses to run without --yes, prints its estimate first, and stops at FIRRA_EVAL_BUDGET_USD.
See docs/runbooks/synthetic-data.md.
"""

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from firra.db import pool as db_pool
from firra.db.synthetic import create_workspace
from firra.db.types import WorkspaceId
from firra.llm.models import ModelSet
from firra.llm.prompts import load_prompt
from firra.llm.service import Llm
from firra.llm.vertex import VertexClient
from firra.storage.raw import LocalRawStore
from firra.synthetic.generate import (
    PROMPT_VERSION,
    CappedUsage,
    Corpus,
    estimate_cost_usd,
    generate_corpus,
)
from firra.synthetic.load import (
    CORPUS_DIR,
    EVENTS_FILE,
    MANIFEST_FILE,
    MESSAGES_FILE,
    load_corpus,
    read_events,
    read_messages,
)
from firra.synthetic.plan import build_plan
from firra.synthetic.spec import load_spec
from firra.synthetic.truth import write_truth


class SyntheticSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FIRRA_", extra="ignore", env_ignore_empty=True)

    gcp_project_id: str | None = None
    vertex_location: str | None = None
    model_synthdata: str | None = None
    # Short-lived token for a run outside Cloud Run: $(gcloud auth print-access-token).
    vertex_access_token: SecretStr | None = None
    eval_budget_usd: Decimal = Decimal(5)
    database_url: SecretStr | None = None
    local_raw_dir: str | None = None


class _StaticToken:
    def __init__(self, token: str) -> None:
        self._token = token

    def token(self) -> str:
        return self._token


def write_corpus(
    corpus: Corpus, manifest: dict[str, object], corpus_dir: Path = CORPUS_DIR
) -> None:
    corpus_dir.mkdir(parents=True, exist_ok=True)
    (corpus_dir / MESSAGES_FILE).write_text(
        "".join(m.model_dump_json(exclude_none=True) + "\n" for m in corpus.messages)
    )
    (corpus_dir / EVENTS_FILE).write_text(
        "".join(e.model_dump_json(exclude_none=True) + "\n" for e in corpus.events)
    )
    (corpus_dir / MANIFEST_FILE).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def _plan_command(settings: SyntheticSettings) -> int:
    spec = load_spec()
    plan = build_plan(spec)
    per_client = Counter[str]()
    for thread in plan.threads:
        per_client[thread.client] += len(thread.messages)
    print(
        f"{spec.version}: {plan.message_count} messages, {len(plan.threads)} threads, "
        f"{len(plan.meetings)} meetings"
    )
    for key, count in sorted(per_client.items()):
        print(f"  {key}: {count}")
    model = settings.model_synthdata or "gemini-3.8-flash"
    estimate = estimate_cost_usd(plan, model, datetime.now(UTC).date())
    cap = settings.eval_budget_usd
    print(f"generation estimate with {model}: about {estimate:.2f} USD (cap {cap} USD)")
    return 0


def _generate_command(settings: SyntheticSettings, confirmed: bool) -> int:
    spec = load_spec()
    plan = build_plan(spec)
    model = settings.model_synthdata
    if not (
        settings.gcp_project_id
        and settings.vertex_location
        and model
        and settings.vertex_access_token
    ):
        print(
            "set FIRRA_GCP_PROJECT_ID, FIRRA_VERTEX_LOCATION, FIRRA_MODEL_SYNTHDATA and "
            "FIRRA_VERTEX_ACCESS_TOKEN",
            file=sys.stderr,
        )
        return 2
    estimate = estimate_cost_usd(plan, model, datetime.now(UTC).date())
    print(f"estimate: about {estimate:.2f} USD, hard cap {settings.eval_budget_usd} USD")
    if not confirmed:
        print("nothing was generated; re-run with --yes to spend it", file=sys.stderr)
        return 1
    usage = CappedUsage(cap_usd=settings.eval_budget_usd)
    with httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0)) as http:
        llm = Llm(
            backend=VertexClient(
                project_id=settings.gcp_project_id,
                location=settings.vertex_location,
                http=http,
                tokens=_StaticToken(settings.vertex_access_token.get_secret_value()),
            ),
            models=ModelSet(
                location=settings.vertex_location,
                extract=None,
                synth=None,
                embed=None,
                synthdata=model,
            ),
            usage=usage,
        )
        corpus = generate_corpus(spec, plan, llm, load_prompt(PROMPT_VERSION))
    write_corpus(
        corpus,
        {
            "spec_version": spec.version,
            "model": model,
            "prompt_version": PROMPT_VERSION,
            "messages": len(corpus.messages),
            "events": len(corpus.events),
            "calls": usage.calls,
            "cost_usd": str(usage.spent),
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        },
    )
    print(
        f"wrote {len(corpus.messages)} messages and {len(corpus.events)} events; "
        f"{usage.calls} calls, {usage.spent:.2f} USD"
    )
    return 0


def _load_command(settings: SyntheticSettings, workspace: str | None, create: str | None) -> int:
    if settings.database_url is None or settings.local_raw_dir is None:
        print("set FIRRA_DATABASE_URL and FIRRA_LOCAL_RAW_DIR", file=sys.stderr)
        return 2
    spec = load_spec()
    pool = db_pool.create_pool_for(settings.database_url.get_secret_value())
    pool.open()
    try:
        if create:
            workspace_id = WorkspaceId(uuid4())
            create_workspace(pool, workspace_id=workspace_id, name=create)
        else:
            workspace_id = WorkspaceId(UUID(workspace or ""))
        result = load_corpus(
            pool,
            LocalRawStore(Path(settings.local_raw_dir)),
            spec,
            workspace_id=workspace_id,
            messages=read_messages(),
            events=read_events(),
        )
    finally:
        pool.close()
    print(json.dumps({"workspace_id": str(workspace_id), **result}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m firra.synthetic")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("plan", help="show the plan and the generation estimate (free)")
    commands.add_parser("truth", help="rewrite synthetic/northbeam/truth.json (free)")
    generate = commands.add_parser("generate", help="write bodies with Gemini (costs money)")
    generate.add_argument("--yes", action="store_true", help="confirm the spend")
    load = commands.add_parser("load", help="load the committed corpus into a workspace")
    target = load.add_mutually_exclusive_group(required=True)
    target.add_argument("--workspace-id")
    target.add_argument("--create", metavar="NAME", help="create a new workspace with this name")
    args = parser.parse_args(argv)

    settings = SyntheticSettings()
    if args.command == "plan":
        return _plan_command(settings)
    if args.command == "truth":
        spec = load_spec()
        write_truth(spec, build_plan(spec))
        return 0
    if args.command == "generate":
        return _generate_command(settings, args.yes)
    return _load_command(settings, args.workspace_id, args.create)


if __name__ == "__main__":
    sys.exit(main())
