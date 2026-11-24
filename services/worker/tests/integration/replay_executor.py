"""ReplayExecutor: answers packets with Northbeam ground truth (L3-06a).

Reads the committed corpus and truth files, never the implementation under
test. Each packet block is matched back to its corpus message by the header
(sender, subject, date); the table is asserted to be exactly one match, so a
mismatch fails loudly instead of replaying the wrong items. Kind-specific
fields come from the spec scenarios (the truth file carries kind+quote only).
"""

import json
import re
from pathlib import Path
from typing import Any

from firra.ai.executor import ExecutorKind, ExecutorResult

_HANDLE = re.compile(r'id="(r\d+)"')
_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_SUBJECT = re.compile(r"^Subject: (.*)$", re.MULTILINE)
_DATE = re.compile(r'date="(\d{4}-\d{2}-\d{2})"')


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text())


class ReplayExecutor:
    """Ground-truth extractions for recorded-output executor evals."""

    kind = ExecutorKind.codex

    def __init__(
        self,
        corpus_dir: Path,
        spec_path: Path,
        truth_path: Path,
        *,
        corrupt_every: int = 0,
        corrupt_marker: str = " xyzzy",
    ) -> None:
        import yaml  # type: ignore[import-untyped]

        messages: list[dict[str, Any]] = [
            json.loads(line)
            for line in (corpus_dir / "messages.jsonl").read_text().splitlines()
            if line.strip()
        ]
        truth = _load_json(truth_path)
        spec = yaml.safe_load(spec_path.read_text())
        spec_fields: dict[tuple[str, str], dict[str, Any]] = {}
        for scenario in spec["scenarios"]:
            for thread in scenario.get("threads", []):
                for message in thread.get("messages", []):
                    for expected in message.get("expect", []) or []:
                        spec_fields[(expected.get("kind"), expected.get("quote"))] = expected
        self._items: dict[str, list[dict[str, Any]]] = {}
        unmatched: list[str] = []
        for item in truth["items"]:
            key = (item["kind"], item["quote"])
            if key not in spec_fields:
                unmatched.append(f"{item['kind']}: {item['quote'][:60]}")
                continue
            self._items.setdefault(item["external_id"], []).append(
                {**item, "spec": spec_fields[key]}
            )
        assert not unmatched, f"truth items without a spec scenario: {unmatched}"
        self._messages = messages
        self._emitted = 0
        self._corrupt_every = corrupt_every
        self._corrupt_marker = corrupt_marker
        self.corrupted: list[str] = []

    def _match(self, block: str) -> dict[str, Any]:
        from_match = re.search(r"^From: (\S+)", block, re.MULTILINE)
        sender = from_match.group(1).lower() if from_match else ""
        subject_match = _SUBJECT.search(block)
        subject = subject_match.group(1) if subject_match else ""
        date_match = _DATE.search(block)
        date = date_match.group(1) if date_match else ""
        candidates = [
            m
            for m in self._messages
            if m["sender"]["email"].lower() == sender
            and (m.get("subject") or "") == subject
            and str(m["occurred_at"])[:10] == date
        ]
        if len(candidates) != 1:
            collapsed = re.sub(r"\s+", " ", block)
            narrowed = [
                m
                for m in candidates
                if re.sub(r"\s+", " ", str(m.get("body_text") or "")[:120]) in collapsed
            ]
            if len(narrowed) == 1:
                return narrowed[0]
            candidates = narrowed
        if len(candidates) != 1:
            from firra.clean.email import clean_email

            collapsed = re.sub(r"\s+", " ", block)
            cleaned = [
                m
                for m in candidates
                if re.sub(r"\s+", " ", clean_email(str(m.get("body_text") or "")).body) in collapsed
            ]
            if len(cleaned) == 1:
                return cleaned[0]
            if (
                cleaned
                and len(
                    {
                        json.dumps(
                            sorted(
                                (item["kind"], item["quote"])
                                for item in self._items.get(m["external_id"], [])
                            )
                        )
                        for m in cleaned
                    }
                )
                == 1
            ):
                return cleaned[0]
            candidates = cleaned
        if len(candidates) != 1:
            snippet = re.sub(r"\s+", " ", block[:200])
            raise AssertionError(
                f"packet block matches {len(candidates)} corpus messages, want 1: {snippet}"
            )
        return candidates[0]

    def _item_dict(self, item: dict[str, Any], sender: str) -> dict[str, Any]:
        spec = item["spec"]
        quote = item["quote"]
        self._emitted += 1
        if self._corrupt_every and self._emitted % self._corrupt_every == 0:
            quote += self._corrupt_marker
            self.corrupted.append(quote)
        due = spec.get("due_date")
        return {
            "kind": item["kind"],
            "quote": quote,
            "speaker": sender,
            "text": item["quote"][:250],
            "subtype": spec.get("subtype"),
            "tone": spec.get("tone"),
            "owner_side": spec.get("owner_side"),
            "due_date": due.isoformat() if hasattr(due, "isoformat") else due,
            "metric": spec.get("metric"),
            "change": spec.get("change"),
            "person_name": spec.get("person_name"),
            "role_title": spec.get("role_title"),
            "budget_direction": spec.get("budget_direction"),
            "initiative_type": spec.get("initiative_type"),
            "topic_key": None,
        }

    def run(
        self, prompt: str, packet_text: str, schema: dict[str, Any], *, timeout_seconds: float
    ) -> ExecutorResult:
        handles = _HANDLE.findall(packet_text)
        assert handles, "packet carries no handles"
        positions = [m.start() for m in _HANDLE.finditer(packet_text)]
        results = []
        for index, handle in enumerate(handles):
            end = positions[index + 1] if index + 1 < len(positions) else len(packet_text)
            message = self._match(packet_text[positions[index] : end])
            sender = message["sender"]["email"]
            items = [
                self._item_dict(item, sender)
                for item in self._items.get(message["external_id"], [])
            ]
            results.append({"record": handle, "items": items})
        return ExecutorResult(
            json.dumps({"results": results}),
            model="replay-northbeam",
            input_tokens=0,
            output_tokens=0,
        )
