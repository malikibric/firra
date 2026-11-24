"""Prompt lab: sample selection, scoring of a fake model, and the free preview."""

import json
from decimal import Decimal
from typing import Any

import httpx
import pytest

from firra.clean import clean_email
from firra.evals.__main__ import main
from firra.evals.lab import (
    NotSyntheticError,
    eligible,
    ensure_synthetic,
    render,
    run_lab,
    select_sample,
    write_result,
)
from firra.evals.models import Truth
from firra.extract.process import PROMPT_VERSION
from firra.llm.models import ModelSet
from firra.llm.prompts import load_prompt
from firra.llm.service import Llm
from firra.llm.vertex import Generation, Usage
from firra.synthetic.generate import CappedUsage
from firra.synthetic.load import read_messages
from firra.synthetic.spec import load_spec
from firra.synthetic.truth import TRUTH_PATH

SPEC = load_spec()
MESSAGES = read_messages()
TRUTH = Truth.model_validate_json(TRUTH_PATH.read_text())
SIMPLE_KINDS = {"request", "praise", "complaint", "goal", "result_reported", "initiative"}


def _sample(routine: int = 12, seed: int = 1) -> Any:
    return select_sample(MESSAGES, TRUTH, SPEC, routine=routine, seed=seed)


def test_sample_holds_every_eligible_planted_message_and_a_seeded_routine_subset() -> None:
    first, again, other = _sample(), _sample(), _sample(seed=2)
    assert [m.external_id for m in first.routine] == [m.external_id for m in again.routine]
    assert {m.external_id for m in first.routine} != {m.external_id for m in other.routine}
    assert len(first.routine) == 12
    planted = {i.external_id for i in TRUTH.items} | {q.external_id for q in TRUTH.forbidden_quotes}
    # every eligible planted message, none missing
    assert {m.external_id for m in first.planted} == {
        m.external_id for m in MESSAGES if eligible(m, SPEC) and m.external_id in planted
    }
    assert len(first.planted) > 30
    assert not {m.external_id for m in first.routine} & planted
    assert all(eligible(m, SPEC) for m in [*first.planted, *first.routine])


def test_metadata_only_clients_never_reach_the_lab() -> None:
    hidden = {c.domain for c in SPEC.clients if c.policy != "full"}
    assert hidden, "the spec has a metadata_only client"
    sample = _sample(routine=700)
    for message in [*sample.planted, *sample.routine]:
        people = [message.sender.email, *(p.email for p in [*message.to, *message.cc])]
        assert not all(
            a.rsplit("@", 1)[-1] in hidden or a.endswith("northbeam.example") for a in people
        )


class _Fake:
    """Answers each message with its planted truth items plus one extra request on routine mail."""

    def __init__(self, planted_ids: set[str], rename: dict[str, str] | None = None) -> None:
        self.rename = rename or {}
        self.by_start = {clean_email(m.body_text).body: m for m in MESSAGES if m.body_text}
        self.truth: dict[str, list[Any]] = {}
        for item in TRUTH.items:
            self.truth.setdefault(item.external_id, []).append(item)
        self.planted_ids = planted_ids

    def generate(self, model: str, *, user: str, **_: Any) -> Generation:
        target = user.split('<source_record id="target">', 1)[1]  # context blocks come first
        message = next(m for start, m in self.by_start.items() if start in target)
        items: list[dict[str, Any]] = [
            {
                "kind": self.rename.get(item.kind, item.kind),
                "quote": item.quote,
                "speaker": message.sender.email,
                "text": "A planted item.",
                "subtype": item.subtype,
                "initiative_type": item.initiative_type,
            }
            for item in self.truth.get(message.external_id, [])
            if item.kind in SIMPLE_KINDS
        ]
        if message.external_id not in self.planted_ids:
            words = clean_email(message.body_text or "").body.split()
            items.append(
                {
                    "kind": "request",
                    "quote": " ".join(words[3:11]),
                    "speaker": message.sender.email,
                    "text": "A routine question.",
                }
            )
        return Generation(
            text=json.dumps({"items": items}), usage=Usage(input_tokens=1000, output_tokens=100)
        )

    def embed(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the lab never embeds")


def test_lab_reports_recall_on_planted_items_and_noise_on_routine_mail() -> None:
    sample = _sample(routine=10)
    usage = CappedUsage(cap_usd=Decimal("1"))
    llm = Llm(
        backend=_Fake({m.external_id for m in sample.planted}),
        models=ModelSet(
            location="eu", extract="gemini-3.5-flash-lite", synth=None, embed=None, synthdata=None
        ),
        usage=usage,
    )
    result = run_lab(llm, load_prompt(PROMPT_VERSION), sample, TRUTH, SPEC, messages=MESSAGES)
    rows = {row.kind: row for row in result.by_kind}
    assert result.errors == 0
    assert result.planted_messages == len(sample.planted) and result.routine_messages == 10
    for kind in SIMPLE_KINDS & {r.kind for r in result.by_kind if r.expected}:
        assert rows[kind].recall == 1.0, kind
    assert rows["request"].extra_on_routine == 10
    assert rows["request"].extra_per_100_routine == 100.0
    assert rows["request"].extra_on_planted == 0
    assert result.input_tokens == 1000 * (len(sample.planted) + 10)
    assert result.cost_usd == usage.spent > 0
    assert any("routine question" not in q for q in result.extra_quotes["request"])
    text = render(result)
    assert "extra request on routine mail" in text and "recall=" in text
    # Missing items are named by id and kind only (commitment/tone/... were not answered).
    assert any(":commitment:" in m for m in result.missing)


def test_preview_spends_nothing_and_needs_no_credentials(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("FIRRA_GCP_PROJECT_ID", "FIRRA_VERTEX_ACCESS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    assert main(["prompt-lab", "--routine", "5"]) == 0
    out = capsys.readouterr().out
    assert "estimate up to about" in out and "nothing was sent" in out
    assert main(["prompt-lab", "--routine", "5", "--thinking-budget", "512"]) == 0
    assert "thinking budget 512" in capsys.readouterr().out


def test_spending_without_credentials_is_refused(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("FIRRA_GCP_PROJECT_ID", "FIRRA_VERTEX_ACCESS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    assert main(["prompt-lab", "--routine", "5", "--yes"]) == 2
    assert "FIRRA_VERTEX_ACCESS_TOKEN" in capsys.readouterr().err


def test_a_message_that_cannot_be_answered_is_counted_and_the_run_goes_on() -> None:
    sample = _sample(routine=2)
    fake = _Fake({m.external_id for m in sample.planted})
    calls = 0
    answer = fake.generate

    def flaky(model: str, *, user: str, **kwargs: Any) -> Generation:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise httpx.ReadTimeout("slow")
        return answer(model, user=user, **kwargs)

    fake.generate = flaky  # type: ignore[method-assign]
    llm = Llm(
        backend=fake,
        models=ModelSet(
            location="eu", extract="gemini-3.5-flash-lite", synth=None, embed=None, synthdata=None
        ),
        usage=CappedUsage(cap_usd=Decimal("1")),
    )
    result = run_lab(llm, load_prompt(PROMPT_VERSION), sample, TRUTH, SPEC, messages=MESSAGES)
    assert result.errors == 1
    assert result.extraction.matched > 0


def _llm(fake: Any) -> Llm:
    return Llm(
        backend=fake,
        models=ModelSet(
            location="eu", extract="gemini-3.5-flash-lite", synth=None, embed=None, synthdata=None
        ),
        usage=CappedUsage(cap_usd=Decimal("1")),
    )


def test_routine_noise_is_not_available_when_no_routine_message_was_completed() -> None:
    sample = _sample(routine=10)
    fake = _Fake({m.external_id for m in sample.planted})
    routine = {m.external_id for m in sample.routine}
    answer = fake.generate

    def only_planted(model: str, *, user: str, **kwargs: Any) -> Generation:
        target = user.split('<source_record id="target">', 1)[1]
        if any(
            clean_email(m.body_text or "").body in target
            for m in sample.routine
            if m.external_id in routine
        ):
            raise httpx.ReadTimeout("slow")
        return answer(model, user=user, **kwargs)

    fake.generate = only_planted  # type: ignore[method-assign]
    result = run_lab(
        _llm(fake), load_prompt(PROMPT_VERSION), sample, TRUTH, SPEC, messages=MESSAGES
    )
    rows = {row.kind: row for row in result.by_kind}
    assert result.incomplete and result.routine_completed == 0
    assert result.errors == 10 and result.planted_completed == len(sample.planted)
    assert rows["request"].extra_per_100_routine is None
    assert "INCOMPLETE RUN" in render(result) and "n/a" in render(result)


def test_only_the_fictional_corpus_is_accepted() -> None:
    ensure_synthetic(MESSAGES)
    real = MESSAGES[0].model_copy(
        update={
            "external_id": "<abc@mail.gmail.com>",
            "sender": MESSAGES[0].sender.model_copy(update={"email": "jane@acme.com"}),
        }
    )
    with pytest.raises(NotSyntheticError):
        ensure_synthetic([*MESSAGES[:3], real])
    realistic_address = MESSAGES[0].model_copy(
        update={"to": [MESSAGES[0].to[0].model_copy(update={"email": "ana@northbeam.com"})]}
    )
    with pytest.raises(NotSyntheticError):
        ensure_synthetic([realistic_address])


def test_a_need_item_is_stored_as_a_request_and_scored_as_the_planted_one(tmp_path: Any) -> None:
    sample = _sample(routine=3)
    fake = _Fake({m.external_id for m in sample.planted}, rename={"request": "need"})
    result = run_lab(
        _llm(fake), load_prompt(PROMPT_VERSION), sample, TRUTH, SPEC, messages=MESSAGES, seed=7
    )
    rows = {row.kind: row for row in result.by_kind}
    assert rows["request"].recall == 1.0
    # the 12 planted requests were answered as needs; the only plain requests are the fake's
    # routine extras (one per routine message)
    assert result.raw_kinds["need"] == 12 and result.raw_kinds["request"] == 3
    write_result(result, tmp_path)
    write_result(result.model_copy(update={"seed": 8}), tmp_path)
    names = sorted(p.name for p in tmp_path.iterdir())
    assert len(names) == 4
    assert any("seed7" in n for n in names) and any("seed8" in n for n in names)
