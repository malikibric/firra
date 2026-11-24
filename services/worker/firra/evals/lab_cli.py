"""``python -m firra.evals prompt-lab``: sample, estimate, and (with --yes) run the lab."""

import argparse
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from firra.evals.lab import (
    NotSyntheticError,
    ensure_synthetic,
    render,
    run_lab,
    select_sample,
    write_result,
)
from firra.evals.models import Truth
from firra.extract.process import PROMPT_VERSION
from firra.llm.models import ModelSet
from firra.llm.prices import cost_usd
from firra.llm.prompts import load_prompt
from firra.llm.service import Llm
from firra.llm.vertex import VertexClient
from firra.synthetic.generate import CappedUsage
from firra.synthetic.load import CORPUS_DIR, read_messages
from firra.synthetic.spec import load_spec
from firra.synthetic.truth import TRUTH_PATH

# Measured on the first real run: 1,966 input and about 115 output tokens per message.
ESTIMATE_INPUT_TOKENS = 2_000
ESTIMATE_OUTPUT_TOKENS = 150


class LabSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FIRRA_", extra="ignore", env_ignore_empty=True)

    gcp_project_id: str | None = None
    vertex_location: str = "eu"
    model_extract: str = "gemini-3.5-flash-lite"
    # Short-lived token: FIRRA_VERTEX_ACCESS_TOKEN="$(gcloud auth print-access-token)".
    vertex_access_token: SecretStr | None = None


class StaticToken:
    def __init__(self, value: str) -> None:
        self._value = value

    def token(self) -> str:
        return self._value


def add_arguments(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    lab = commands.add_parser("prompt-lab", help="extraction only on a sample, a few cents")
    lab.add_argument("--prompt", default=PROMPT_VERSION, help="prompt version, e.g. extract.v3")
    lab.add_argument("--routine", type=int, default=60, help="routine messages to add")
    lab.add_argument("--seed", type=int, default=1)
    lab.add_argument(
        "--thinking-budget",
        type=int,
        default=0,
        help="thinking tokens per message; 0 = off, -1 = do not send the setting",
    )
    lab.add_argument("--budget-usd", type=Decimal, default=Decimal("0.30"))
    lab.add_argument("--out", type=Path, default=Path("lab-out"))
    lab.add_argument("--corpus-dir", type=Path, default=CORPUS_DIR)
    lab.add_argument("--yes", action="store_true", help="spend: call the real model")


def run_command(args: argparse.Namespace) -> int:
    settings = LabSettings()
    messages = read_messages(args.corpus_dir)
    try:
        ensure_synthetic(messages)
    except NotSyntheticError:
        sys.stderr.write("refusing: the lab only sends the synthetic Northbeam corpus\n")
        return 2
    truth = Truth.model_validate_json(TRUTH_PATH.read_text())
    spec = load_spec()
    prompt = load_prompt(args.prompt)
    sample = select_sample(messages, truth, spec, routine=args.routine, seed=args.seed)
    count = len(sample.planted) + len(sample.routine)
    estimate = cost_usd(
        settings.model_extract,
        input_tokens=count * ESTIMATE_INPUT_TOKENS,
        output_tokens=count * (ESTIMATE_OUTPUT_TOKENS + max(args.thinking_budget, 0)),
        on=datetime.now(UTC).date(),
    )
    sys.stdout.write(
        f"prompt {prompt.version}, model {settings.model_extract}: {len(sample.planted)} planted + "
        f"{len(sample.routine)} routine messages, thinking budget {args.thinking_budget}, "
        f"estimate up to about {estimate:.3f} USD (hard cap {args.budget_usd} USD)\n"
    )
    if not args.yes:
        sys.stdout.write("nothing was sent; re-run with --yes to spend it\n")
        return 0
    if not (settings.gcp_project_id and settings.vertex_access_token):
        sys.stderr.write("set FIRRA_GCP_PROJECT_ID and FIRRA_VERTEX_ACCESS_TOKEN\n")
        return 2
    usage = CappedUsage(cap_usd=args.budget_usd)
    with httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0)) as http:
        llm = Llm(
            backend=VertexClient(
                project_id=settings.gcp_project_id,
                location=settings.vertex_location,
                http=http,
                tokens=StaticToken(settings.vertex_access_token.get_secret_value()),
            ),
            models=ModelSet(
                location=settings.vertex_location,
                extract=settings.model_extract,
                synth=None,
                embed=None,
                synthdata=None,
            ),
            usage=usage,
        )

        def progress(done: int, total: int) -> None:
            if done % 20 == 0 or done == total:
                sys.stdout.write(f"  {done}/{total} messages\n")
                sys.stdout.flush()

        result = run_lab(
            llm,
            prompt,
            sample,
            truth,
            spec,
            messages=messages,
            thinking_budget=None if args.thinking_budget < 0 else args.thinking_budget,
            seed=args.seed,
            on_progress=progress,
        )
    write_result(result, args.out)
    sys.stdout.write("\n" + render(result) + "\n")
    return 1 if result.incomplete else 0
