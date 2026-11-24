"""Prompt lab: extraction only, no database, a few cents per iteration.

The full eval (about 650 model calls plus replay) is too slow and costly to iterate a prompt on. The
lab sends a sample to the real model through the same prompt, schema, cleaning and verification as
production: every message that carries planted truth items, plus a seeded sample of routine mail.
It reports recall per kind on the planted items and the noise per kind on routine mail, which is
what decides whether latent needs and expansion states hold up on real model output.

Synthetic corpus only. Quotes of the model's extra items are printed locally so the prompt can be
corrected; they are never persisted outside the output directory.
"""

import hashlib
import json
import random
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict

from firra.clean import clean_email
from firra.db.processing import PendingRecord
from firra.db.types import WorkspaceId
from firra.evals.models import ItemObs, PrecisionRecall, Truth
from firra.evals.score import extraction_report
from firra.extract.items import RecordFacts, accept_items
from firra.extract.process import build_user_message
from firra.extract.schema import ExtractionOutput
from firra.http import ProviderError
from firra.llm.prompts import Prompt
from firra.llm.service import BudgetExceededError, Llm, StructuredOutputError
from firra.llm.vertex import LlmBlockedError
from firra.synthetic.spec import Spec
from firra_contracts import Message

CONTEXT_MESSAGES = 2
# Kinds whose extra items feed latent needs, expansion states and signals downstream.
NOISE_KINDS = ("request", "initiative", "commitment", "praise", "result_reported", "tone")
SAMPLE_QUOTES = 12


class LabModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class KindRow(LabModel):
    kind: str
    expected: int
    matched: int
    recall: float
    extra_on_planted: int
    extra_on_routine: int
    extra_per_100_routine: float | None  # None: no routine message was completed


class LabResult(LabModel):
    prompt: str
    model: str
    seed: int
    thinking_budget: int | None
    sample_digest: str
    planted_messages: int
    routine_messages: int
    planted_completed: int
    routine_completed: int
    incomplete: bool
    errors: int
    raw_kinds: dict[str, int]
    dropped: dict[str, int]
    extraction: PrecisionRecall
    by_kind: list[KindRow]
    missing: list[str]
    extra_quotes: dict[str, list[str]]
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal


@dataclass(frozen=True)
class Sample:
    planted: list[Message]
    routine: list[Message]


class NotSyntheticError(ValueError):
    """The lab only ever sends the committed synthetic corpus to a model."""


def ensure_synthetic(messages: Sequence[Message]) -> None:
    """Refuse anything that does not look like the fictional Northbeam corpus (ids and every
    address on a reserved ``.example`` domain). A cheap tripwire against pointing the lab at real
    mail; it is not proof of provenance."""
    for message in messages:
        people = [
            message.sender.email,
            *(p.email for p in [*message.to, *message.cc, *message.bcc]),
        ]
        if not message.external_id.endswith("@synthetic.northbeam.example>") or any(
            not _domain(address).endswith(".example") for address in people
        ):
            raise NotSyntheticError(message.external_id)


def _domain(address: str) -> str:
    return address.rsplit("@", 1)[-1].lower()


def full_policy_domains(spec: Spec) -> frozenset[str]:
    """Client domains the pipeline would send to the model (``metadata_only`` ones never are)."""
    return frozenset(c.domain.lower() for c in spec.clients if c.policy == "full")


def eligible(message: Message, spec: Spec) -> bool:
    people = [message.sender.email, *(p.email for p in [*message.to, *message.cc])]
    return bool(message.body_text) and any(
        _domain(address) in full_policy_domains(spec) for address in people
    )


def select_sample(
    messages: Sequence[Message], truth: Truth, spec: Spec, *, routine: int, seed: int
) -> Sample:
    planted_ids = {item.external_id for item in truth.items} | {
        quote.external_id for quote in truth.forbidden_quotes
    }
    usable = [m for m in messages if eligible(m, spec)]
    planted = [m for m in usable if m.external_id in planted_ids]
    rest = sorted(
        (m for m in usable if m.external_id not in planted_ids), key=lambda m: m.external_id
    )
    picked = random.Random(seed).sample(rest, min(routine, len(rest)))  # noqa: S311 - seeded sample
    return Sample(planted=planted, routine=sorted(picked, key=lambda m: m.occurred_at))


def _record(message: Message, spec: Spec, body: str) -> PendingRecord:
    firm_domain = spec.firm.domain.lower()
    from_firm = _domain(message.sender.email) == firm_domain
    participants = frozenset(p.email.lower() for p in [message.sender, *message.to, *message.cc])
    return PendingRecord(
        id=uuid4(),
        client_id=uuid4(),
        kind="email",
        thread_key=message.thread_key,
        occurred_at=message.occurred_at,
        direction="out" if from_firm else "in",
        body_clean=body,
        subject=message.subject,
        deep_link=None,
        content_hash=None,
        processing_version=0,
        policy="full",
        client_policy="full",
        author_email=message.sender.email,
        author_side="firm" if from_firm else "client",
        participant_emails=participants,
    )


@dataclass
class _Run:
    items: list[ItemObs] = field(default_factory=list)
    kinds_by_message: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    extra_quotes: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    dropped: Counter[str] = field(default_factory=Counter)
    errors: int = 0


def run_lab(
    llm: Llm,
    prompt: Prompt,
    sample: Sample,
    truth: Truth,
    spec: Spec,
    *,
    messages: Sequence[Message],
    thinking_budget: int | None = 0,
    seed: int = 0,
    on_progress: Callable[[int, int], None] | None = None,
) -> LabResult:
    workspace_id = WorkspaceId(uuid4())
    by_thread: dict[str, list[Message]] = defaultdict(list)
    for m in messages:
        by_thread[m.thread_key or m.external_id].append(m)
    run = _Run()
    ordered = [*sample.planted, *sample.routine]
    planted_ids = {m.external_id for m in sample.planted}
    completed_planted = completed_routine = 0
    raw_kinds: Counter[str] = Counter()
    for number, message in enumerate(ordered, start=1):
        body = clean_email(message.body_text or "").body
        earlier = sorted(
            (
                m
                for m in by_thread[message.thread_key or message.external_id]
                if m.occurred_at < message.occurred_at
            ),
            key=lambda m: m.occurred_at,
        )[-CONTEXT_MESSAGES:]
        context = [clean_email(m.body_text or "").body for m in earlier]
        record = _record(message, spec, body)
        try:
            answer = llm.structured(
                workspace_id=workspace_id,
                job_id=None,
                purpose="extract",
                prompt_version=prompt.version,
                system=prompt.text,
                user=build_user_message(record, body, [c for c in context if c]),
                output=ExtractionOutput,
                thinking_budget=thinking_budget,
            )
        except (StructuredOutputError, LlmBlockedError, ProviderError, httpx.HTTPError):
            # One message that cannot be answered (timeout, bad request, blocked) must not end
            # the whole run; it is counted and shown as ``errors``.
            run.errors += 1
            if on_progress is not None:
                on_progress(number, len(ordered))
            continue
        except BudgetExceededError:
            run.errors += len(ordered) - number + 1
            break
        accepted, dropped = accept_items(
            answer.value,
            RecordFacts(
                body=body,
                direction=record.direction,
                author_side=record.author_side,
                participant_emails=record.participant_emails,
            ),
        )
        run.dropped.update(dropped)
        if message.external_id in planted_ids:
            completed_planted += 1
        else:
            completed_routine += 1
        for got in accepted:
            # A statement of need is stored as a request; the lab still shows it as ``need``.
            raw_kinds["need" if got.payload.get("form") == "need" else got.kind] += 1
            run.items.append(
                ItemObs(
                    external_id=message.external_id,
                    kind=got.kind,
                    quote=got.evidence.quote,
                    subtype=got.payload.get("subtype"),
                    budget_direction=got.payload.get("budget_direction"),
                    initiative_type=got.payload.get("initiative_type"),
                )
            )
        if on_progress is not None:
            on_progress(number, len(ordered))

    scoped = truth.model_copy(
        update={"items": [i for i in truth.items if i.external_id in planted_ids]}
    )
    overall, per_kind, missing, extras = extraction_report(scoped, run.items)
    extra_on_planted: Counter[str] = Counter()
    extra_on_routine: Counter[str] = Counter()
    routine_ids = {m.external_id for m in sample.routine}
    for index in extras:
        item = run.items[index]
        if item.external_id in routine_ids:
            extra_on_routine[item.kind] += 1
        else:
            extra_on_planted[item.kind] += 1
        quotes = run.extra_quotes[item.kind]
        if len(quotes) < SAMPLE_QUOTES and item.external_id in routine_ids:
            quotes.append(" ".join(item.quote.split()[:22]))
    rows = [
        KindRow(
            kind=kind,
            expected=pr.expected,
            matched=pr.matched,
            recall=pr.recall,
            extra_on_planted=extra_on_planted[kind],
            extra_on_routine=extra_on_routine[kind],
            extra_per_100_routine=(
                round(100 * extra_on_routine[kind] / completed_routine, 1)
                if completed_routine
                else None
            ),
        )
        for kind, pr in sorted(per_kind.items())
    ]
    usage = getattr(llm.usage, "tokens", [])
    spent: Decimal = getattr(llm.usage, "spent", Decimal(0))
    return LabResult(
        prompt=prompt.version,
        model=llm.models.model_for("extract"),
        seed=seed,
        thinking_budget=thinking_budget,
        sample_digest=hashlib.sha256(
            ("\n".join(sorted(m.external_id for m in ordered)) + prompt.text).encode()
        ).hexdigest()[:12],
        planted_messages=len(sample.planted),
        routine_messages=len(sample.routine),
        planted_completed=completed_planted,
        routine_completed=completed_routine,
        incomplete=run.errors > 0,
        errors=run.errors,
        raw_kinds=dict(sorted(raw_kinds.items())),
        dropped=dict(run.dropped),
        extraction=overall,
        by_kind=rows,
        missing=missing,
        extra_quotes=dict(run.extra_quotes),
        input_tokens=sum(i for i, _ in usage),
        output_tokens=sum(o for _, o in usage),
        cost_usd=spent,
    )


def render(result: LabResult) -> str:
    lines = [
        f"prompt={result.prompt} model={result.model} seed={result.seed} "
        f"thinking={result.thinking_budget} sample={result.sample_digest}",
        f"planted {result.planted_completed}/{result.planted_messages} completed, routine "
        f"{result.routine_completed}/{result.routine_messages} completed, errors={result.errors}, "
        f"cost={result.cost_usd:.4f} USD ({result.input_tokens} in / {result.output_tokens} out)",
        *(
            ["INCOMPLETE RUN: noise is measured on completed routine messages only"]
            if result.incomplete
            else []
        ),
        "raw kinds: " + ", ".join(f"{k}={v}" for k, v in result.raw_kinds.items()),
        f"extraction recall={result.extraction.recall:.3f} "
        f"on {result.extraction.expected} planted items",
        "",
        f"{'kind':32} {'exp':>3} {'hit':>3} {'recall':>6} {'extra@planted':>13} "
        f"{'extra@routine':>13} {'per100':>7}",
    ]
    for row in result.by_kind:
        per100 = "n/a" if row.extra_per_100_routine is None else f"{row.extra_per_100_routine:.1f}"
        lines.append(
            f"{row.kind:32} {row.expected:>3} {row.matched:>3} {row.recall:>6.2f} "
            f"{row.extra_on_planted:>13} {row.extra_on_routine:>13} "
            f"{per100:>7}"
        )
    lines += ["", "missing planted items:", *[f"  - {m}" for m in result.missing]]
    for kind in NOISE_KINDS:
        quotes = result.extra_quotes.get(kind, [])
        if quotes:
            lines += ["", f"extra {kind} on routine mail (sample):", *[f"  · {q}" for q in quotes]]
    return "\n".join(lines)


def write_result(result: LabResult, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    name = (
        f"lab-{result.prompt}-{result.model}-seed{result.seed}"
        f"-think{result.thinking_budget}-{result.sample_digest}"
    )
    (out_dir / f"{name}.json").write_text(
        json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    )
    (out_dir / f"{name}.txt").write_text(render(result) + "\n")
