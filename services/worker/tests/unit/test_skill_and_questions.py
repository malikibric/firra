"""SKILL.md and answer-test.md name only tools that exist (no network, no model)."""

import re
from pathlib import Path

import anyio
from mcp import Client

from firra.local.mcp.server import build_server, error

ROOT = Path(__file__).resolve().parents[4]
SKILL = ROOT / "skills/firra/SKILL.md"
RUNBOOK = ROOT / "docs/runbooks/answer-test.md"

IDENT = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)`")

# Backticked argument and field literals in SKILL.md that are not tool names.
NON_TOOL_FIELDS = frozenset(
    {
        "untrusted_text",
        "untrusted_quote",
        "fact_id",
        "risk_state",
        "play",
        "source_ref",
        "records_left",
        "offline",
        "policy",
        "with_context",
        "client",
        "client_id",
        "query",
        "user_confirmed",
    }
)


def catalog_names() -> set[str]:
    async def run() -> set[str]:
        async with Client(
            build_server(lambda name, args: error(), processing_tools=True)
        ) as client:
            return {t.name for t in (await client.list_tools()).tools}

    return anyio.run(run)


def question_rows(text: str) -> list[list[str]]:
    rows = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 5 and cells[0].isdigit() and cells[1]:
            rows.append(cells)
    return rows


def test_question_count_is_28() -> None:
    rows = question_rows(RUNBOOK.read_text())
    assert [int(row[0]) for row in rows] == list(range(1, 29))


def test_expected_tools_exist_in_catalog() -> None:
    names = catalog_names()
    rows = question_rows(RUNBOOK.read_text())
    assert rows
    for row in rows:
        unknown = set(IDENT.findall(row[2])) - names
        assert not unknown, f"question {row[0]} names unknown tools: {sorted(unknown)}"


def test_skill_tool_names_exist_in_catalog() -> None:
    named = set(IDENT.findall(SKILL.read_text())) - NON_TOOL_FIELDS
    unknown = named - catalog_names()
    assert not unknown, f"SKILL.md names unknown tools: {sorted(unknown)}"


def test_skill_states_untrusted_and_no_loop_rules() -> None:
    text = SKILL.read_text()
    assert "untrusted_text" in text
    assert "untrusted_quote" in text
    assert "Never loop over `get_pending_mail`" in text
