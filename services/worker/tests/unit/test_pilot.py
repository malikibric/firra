"""Pilot preparation uses counts and allowlisted fields only."""

from contextlib import nullcontext
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

from firra.assessment import __main__ as cli
from firra.assessment.pilot import estimate_usd, render_estimate, render_pilot_check

WS = UUID("12345678-0000-0000-0000-000000000001")
RUN = UUID("87654321-0000-0000-0000-000000000001")
PRIVATE = {
    "name": "Synthetic Private Client",
    "body_clean": "Synthetic private body",
    "subject": "Synthetic private subject",
    "quote": "Synthetic private quote",
    "address": "synthetic-private@example.test",
}
ROWS: list[dict[str, Any]] = [
    {"client_id": WS, "status": "active", "emails": 3000, **PRIVATE},
    {"client_id": RUN, "status": "churned", "emails": 1000, **PRIVATE},
]
CLIENTS: list[dict[str, Any]] = [
    {
        "client_id": WS,
        "status": "active",
        "miss_reason": None,
        "low_data": 2,
        "coverage_changed": 1,
        **PRIVATE,
    },
    {
        "client_id": RUN,
        "status": "churned",
        "miss_reason": "coverage_changed",
        "low_data": 0,
        "coverage_changed": 3,
        **PRIVATE,
    },
]
TOTALS: dict[str, Any] = {
    "losses": 1,
    "losses_detected_30d": 0,
    "median_lead_days": None,
    "false_alarm_client_weeks_per_10_clients_per_month": 0.5,
    "flicker": 1,
    **PRIVATE,
}


def test_estimate_formula_split_and_zero() -> None:
    assert estimate_usd(4000) == Decimal("6.76")
    assert estimate_usd(1) == Decimal("0.00169")
    text = render_estimate(ROWS, months=6)
    assert "last 6 months: 4000" in text
    assert "client 12345678 (active): 3000" in text
    assert "client 87654321 (churned): 1000" in text
    assert "estimate_usd = 6.76000" in text
    assert text.endswith(
        "Extraction needs the owner's written go and a hard cap equal to the estimate.\n"
    )
    assert "estimate_usd = 0.00000" in render_estimate([], months=12)
    assert "client 12345678 (prospect): 3000" in render_estimate(
        [{**ROWS[0], "status": "prospect"}], months=12
    )


def test_both_outputs_contain_no_content_even_in_unexpected_fields() -> None:
    estimate = render_estimate(ROWS, months=12)
    check = render_pilot_check(TOTALS, CLIENTS)
    for text in (estimate, check):
        assert all(value not in text for value in PRIVATE.values())
        assert str(WS) not in text and str(RUN) not in text
    assert "losses: 1" in check and "median_lead_days: —" in check
    assert "miss_reason=coverage_changed, low_data=0, coverage_changed=3" in check
    unsafe = {**CLIENTS[0], "status": PRIVATE["name"], "miss_reason": PRIVATE["body_clean"]}
    text = render_pilot_check({"losses": PRIVATE["quote"]}, [unsafe])
    assert "(unknown): miss_reason=none" in text and "losses: —" in text
    assert all(value not in text for value in PRIVATE.values())


class FakeEstimateRepo:
    def __init__(self, pool: Any) -> None:
        pass

    def counts(self, *, workspace_id: UUID, months: int) -> list[dict[str, Any]]:
        assert workspace_id == WS and months == 12
        return ROWS


class FakeReportRepo:
    def __init__(self, pool: Any) -> None:
        pass

    def pilot_run(self, *, workspace_id: UUID, run_id: UUID) -> dict[str, Any]:
        assert workspace_id == WS and run_id == RUN
        return {"status": "succeeded", "totals": TOTALS, **PRIVATE}

    def pilot_clients(self, *, workspace_id: UUID, run_id: UUID) -> list[dict[str, Any]]:
        assert workspace_id == WS and run_id == RUN
        return CLIENTS


@pytest.fixture
def fake_repos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRRA_DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:54999/t19")
    monkeypatch.setattr(cli, "create_pool_for", lambda _: nullcontext(None))
    monkeypatch.setattr(cli, "EstimateRepo", FakeEstimateRepo)
    monkeypatch.setattr(cli, "ReportRepo", FakeReportRepo)


@pytest.mark.parametrize("command", ["estimate", "pilot-check"])
def test_cli_success_and_redaction(
    command: str, fake_repos: None, capsys: pytest.CaptureFixture[str]
) -> None:
    args = [command, "--workspace", str(WS)]
    if command == "pilot-check":
        args += ["--run", str(RUN)]
    assert cli.main(args) == 0
    output = capsys.readouterr()
    assert output.err == "" and all(value not in output.out for value in PRIVATE.values())


@pytest.mark.parametrize("status", [None, "queued", "running", "failed"])
def test_check_refuses_unfinished_or_unknown_run(
    status: str | None,
    fake_repos: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        FakeReportRepo, "pilot_run", lambda *a, **k: None if status is None else {"status": status}
    )
    monkeypatch.setattr(
        FakeReportRepo, "pilot_clients", lambda *a, **k: pytest.fail("read before succeeded")
    )
    assert cli.main(["pilot-check", "--workspace", str(WS), "--run", str(RUN)]) == 2
    output = capsys.readouterr()
    assert output.out == "" and "not found or not finished" in output.err


@pytest.mark.parametrize("command", ["estimate", "pilot-check"])
@pytest.mark.parametrize("url", [None, "postgresql://postgres:secret@remote.example.test/db"])
def test_cli_local_guard(
    command: str,
    url: str | None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    if url:
        monkeypatch.setenv("FIRRA_DATABASE_URL", url)
    else:
        monkeypatch.delenv("FIRRA_DATABASE_URL", raising=False)
    monkeypatch.setattr(cli, "create_pool_for", lambda _: pytest.fail("database must not open"))
    args = [command, "--workspace", str(WS)]
    if command == "pilot-check":
        args += ["--run", str(RUN)]
    assert cli.main(args) == 2
    output = capsys.readouterr()
    assert not output.out and "secret" not in output.err and "remote.example.test" not in output.err


@pytest.mark.parametrize("months", ["0", "-1", "bad"])
def test_invalid_months(months: str) -> None:
    with pytest.raises(SystemExit) as error:
        cli.main(["estimate", "--workspace", str(WS), "--months", months])
    assert error.value.code == 2
