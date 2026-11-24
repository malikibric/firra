"""Run guards and settlement use fakes; no worker or model calls."""

from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml  # type: ignore[import-untyped]

from firra.db.evals import RunState
from firra.evals import run
from firra.evals.__main__ import main
from tests.conftest import LOCAL_SECRET


@dataclass
class Clock:
    now: float = 0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def state(*, active: int = 0, pending: int = 0, failed: bool = False) -> RunState:
    return RunState(
        active,
        pending,
        [],
        [{"type": "process.records", "error_class": "budget_exceeded"}] if failed else [],
        {},
    )


@pytest.fixture
def guarded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRRA_DATABASE_URL", "postgresql://postgres@localhost/test")
    monkeypatch.setenv("FIRRA_LOCAL_TASK_SECRET", LOCAL_SECRET)
    monkeypatch.setattr(run, "read_messages", lambda path: [])
    monkeypatch.setattr(run, "read_events", lambda path: [])

    def forbidden_pool(url: str) -> None:
        raise AssertionError("guard/preview must not open the database")

    monkeypatch.setattr(run, "create_pool_for", forbidden_pool)


def health(status: int = 200, body: object = None) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                status,
                json={"status": "ok"} if body is None else body,
            )
        )
    )


@pytest.mark.parametrize(
    "guard",
    [
        "missing_url",
        "remote_url",
        "missing_corpus",
        "missing_events",
        "unhealthy",
        "wrong_health",
        "unreachable",
        "secret",
        "budget",
        "timeout",
    ],
)
def test_guards_exit_two_without_writes(
    guarded: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, guard: str
) -> None:
    options = run.RunOptions(tmp_path / "out", yes=True)
    http = health()
    if guard == "missing_url":
        monkeypatch.delenv("FIRRA_DATABASE_URL")
    elif guard == "remote_url":
        monkeypatch.setenv("FIRRA_DATABASE_URL", "postgresql://postgres@remote.test/test")
    elif guard in {"missing_corpus", "missing_events"}:

        def missing(path: Path) -> list[Any]:
            raise FileNotFoundError

        monkeypatch.setattr(
            run, "read_messages" if guard == "missing_corpus" else "read_events", missing
        )
    elif guard == "unhealthy":
        http = health(503)
    elif guard == "wrong_health":
        http = health(body={"status": "unavailable"})
    elif guard == "unreachable":

        def unreachable(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("offline")

        http = httpx.Client(transport=httpx.MockTransport(unreachable))
    elif guard == "secret":
        monkeypatch.delenv("FIRRA_LOCAL_TASK_SECRET")
    elif guard == "budget":
        options = replace(options, budget_usd=Decimal(0))
    elif guard == "timeout":
        options = replace(options, timeout_min=float("nan"))
    with http:
        assert run.run_evaluation(options, http=http) == 2
    assert not options.out.exists()


def test_preview_loads_nothing(
    guarded: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with health() as http:
        assert run.run_evaluation(run.RunOptions(tmp_path / "out"), http=http) == 0
    assert "about 1\u20132 USD" in capsys.readouterr().out
    assert not (tmp_path / "out").exists()


def test_settlement_requires_two_quiet_polls_ten_seconds_apart() -> None:
    clock = Clock()
    states = iter([state(), state(active=1), state(), state()])
    result, timed_out = run.wait_for_settle(
        lambda: next(states), deadline=100, clock=clock, sleep=clock.sleep
    )
    assert result.active == 0 and not timed_out and clock.now == 30


def test_pending_records_prevent_settlement_and_time_out() -> None:
    clock = Clock()
    result, timed_out = run.wait_for_settle(
        lambda: state(pending=1), deadline=15, clock=clock, sleep=clock.sleep
    )
    assert timed_out and result.pending == 1 and clock.now == 15
    assert run.outcome_code(result, timed_out) == 3


def test_failed_jobs_stop_without_waiting_or_scoring() -> None:
    clock = Clock()
    result, timed_out = run.wait_for_settle(
        lambda: state(failed=True), deadline=100, clock=clock, sleep=clock.sleep
    )
    assert run.outcome_code(result, timed_out) == 3 and clock.now == 0


@pytest.mark.parametrize("value", [state(pending=1), state(active=1), state(failed=True)])
def test_unfinished_outcome_is_three(value: RunState) -> None:
    assert run.outcome_code(value, False) == 3


def test_cli_budget_uses_env_and_explicit_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    budgets = []

    def fake_run(options: run.RunOptions, **kwargs: Any) -> int:
        budgets.append(options.budget_usd)
        return 0

    monkeypatch.setattr("firra.evals.__main__.run_evaluation", fake_run)
    monkeypatch.delenv("FIRRA_EVAL_BUDGET_USD", raising=False)
    assert main(["run", "--out", str(tmp_path)]) == 0
    assert run.RunOptions(out=tmp_path).budget_usd == Decimal("1.75")
    monkeypatch.setenv("FIRRA_EVAL_BUDGET_USD", "2")
    assert main(["run", "--out", str(tmp_path)]) == 0
    assert main(["run", "--out", str(tmp_path), "--budget-usd", "3"]) == 0
    assert budgets == [Decimal("1.75"), Decimal(2), Decimal(3)]
    monkeypatch.setenv("FIRRA_EVAL_BUDGET_USD", "invalid")
    assert main(["run", "--out", str(tmp_path)]) == 2


def test_workflow_spend_gate_and_corpus_check_precede_authentication() -> None:
    workflow = yaml.safe_load(
        (Path(__file__).parents[4] / ".github/workflows/evals.yml").read_text(),
    )
    job = workflow["jobs"]["evals"]
    assert job["if"] == (
        "(github.event_name == 'workflow_dispatch' && inputs.confirm == 'spend') || "
        "(github.event_name == 'schedule' && vars.EVALS_SCHEDULE == 'on')"
    )
    assert "schedule" not in workflow[True]  # manual only: Local v1 spends no Actions minutes
    assert workflow[True]["workflow_dispatch"]["inputs"]["confirm"]["required"] is True
    assert "EVALS_SCHEDULE" not in job["env"]
    steps = job["steps"]
    check = next(
        i for i, step in enumerate(steps) if "Require committed corpus" in step.get("name", "")
    )
    auth = next(
        i
        for i, step in enumerate(steps)
        if step.get("uses", "").startswith("google-github-actions/auth@")
    )
    assert check < auth
    start = next(step for step in steps if step.get("name", "").startswith("Start worker"))
    assert 'FIRRA_LLM_HARD_CAP_USD="$FIRRA_EVAL_BUDGET_USD"' in start["run"]


@pytest.mark.parametrize(
    "budget, expected",
    [("1.75", 0), ("2", 0), ("2.01", 2), ("5", 2), ("NaN", 2), ("0", 2), ("invalid", 2)],
)
def test_workflow_budget_guard(budget: str, expected: int, monkeypatch: pytest.MonkeyPatch) -> None:
    workflow = yaml.safe_load(
        (Path(__file__).parents[4] / ".github/workflows/evals.yml").read_text()
    )
    inputs = workflow[True]["workflow_dispatch"]["inputs"]
    assert inputs["budget_usd"]["default"] == "1.75"
    job = workflow["jobs"]["evals"]
    assert "inputs.budget_usd" in job["env"]["FIRRA_EVAL_BUDGET_USD"]
    assert "vars.EVALS_BUDGET_USD" in job["env"]["FIRRA_EVAL_BUDGET_USD"]
    guard = job["steps"][0]["run"].split("<<'PYTHON'\n", 1)[1].rsplit("PYTHON", 1)[0]
    monkeypatch.setenv("FIRRA_EVAL_BUDGET_USD", budget)
    if expected:
        with pytest.raises(SystemExit) as error:
            exec(guard, {})  # noqa: S102 - execute only the committed workflow guard, no APIs
        assert error.value.code == expected
    else:
        exec(guard, {})  # noqa: S102 - execute only the committed workflow guard, no APIs
