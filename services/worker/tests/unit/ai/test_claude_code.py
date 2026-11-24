import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from firra.ai.claude_code import ClaudeCodeExecutor, _binary
from firra.ai.executor import (
    ExecutorFailed,
    ExecutorLimitReached,
    ExecutorNotInstalled,
    ExecutorNotSignedIn,
    ExecutorTimeout,
)
from firra_contracts import AiExtractionOutput

SCHEMA_PATH = (
    Path(__file__).resolve().parents[5]
    / "packages/contracts/schemas/local/ai_extraction_output.json"
)


@pytest.fixture
def schema() -> dict[str, Any]:
    contract: dict[str, Any] = json.loads(SCHEMA_PATH.read_text())
    return {key: value for key, value in contract.items() if key not in ("$schema", "$id")}


@pytest.fixture
def cli(tmp_path: Path) -> Path:
    source = Path(__file__).resolve().parents[2] / "fixtures/fake_claude.py"
    binary = tmp_path / "claude"
    binary.write_text(f"#!{sys.executable}\n" + source.read_text())
    binary.chmod(0o700)
    scenario(binary, "success")
    return binary


def scenario(binary: Path, name: str, **kwargs: Any) -> None:
    (binary.parent / "scenario.json").write_text(json.dumps({"name": name, **kwargs}))


def run(binary: Path, schema: dict[str, Any], *, timeout: float = 5) -> Any:
    return ClaudeCodeExecutor(binary).run(
        "synthetic prompt", "synthetic packet", schema, timeout_seconds=timeout
    )


@pytest.mark.parametrize("name", ["success", "result"])
def test_success_uses_contract_and_reports_model_tokens(
    cli: Path, schema: dict[str, Any], name: str
) -> None:
    scenario(cli, name)
    result = run(cli, schema)
    parsed = AiExtractionOutput.model_validate_json(result.raw, strict=True)
    assert parsed.results[0].record == "r1"
    assert (result.model, result.input_tokens, result.output_tokens) == (
        "claude-haiku-fixture",
        100,
        20,
    )


def test_exact_argv_empty_private_removed_cwd_clean_environment_and_stdin(
    cli: Path, schema: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("ANTHROPIC_API_KEY", "PGPASSWORD", "FIRRA_SECRET_TEST"):
        monkeypatch.setenv(name, "fixture-secret-never-forward")
    passed: list[dict[str, str]] = []
    popen = subprocess.Popen

    def capture_environment(*args: Any, **kwargs: Any) -> Any:
        passed.append(dict(kwargs["env"]))
        return popen(*args, **kwargs)

    monkeypatch.setattr("firra.ai.claude_code.subprocess.Popen", capture_environment)
    run(cli, schema)
    trace = json.loads((cli.parent / "trace.json").read_text())
    assert trace["argv"] == [
        "-p",
        "--model",
        "haiku",
        "--tools",
        "",
        "--strict-mcp-config",
        "--setting-sources",
        "",
        "--disable-slash-commands",
        "--no-session-persistence",
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(schema, separators=(",", ":")),
    ]
    assert set(passed[0]) == {"HOME", "PATH", "USER", "TMPDIR", "LANG"}
    # Python's macOS framework can add this variable after exec; it is not passed by Firra.
    assert set(trace["environment"]) - {"__CF_USER_TEXT_ENCODING"} == set(passed[0])
    assert "fixture-secret-never-forward" not in str(trace["environment"])
    home = trace["environment"]["HOME"]
    assert (
        trace["environment"]["PATH"]
        == f"/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin:{home}/.local/bin"
    )
    assert trace["environment"]["LANG"] == "en_US.UTF-8"
    assert trace["environment"]["USER"]
    assert trace["files"] == [] and trace["mode"] == 0o700
    assert trace["cwd"] != str(Path.cwd()) and not Path(trace["cwd"]).exists()
    assert trace["stdin"] == "synthetic prompt\n\nsynthetic packet"


@pytest.mark.parametrize(
    "name,expected",
    [
        ("logged_out", ExecutorNotSignedIn),
        ("limit", ExecutorLimitReached),
        ("malformed", ExecutorFailed),
        ("exit", ExecutorFailed),
        ("failure", ExecutorFailed),
        ("huge_stdout", ExecutorFailed),
        ("huge_stderr", ExecutorFailed),
    ],
)
def test_failure_classification_has_no_cli_text(
    cli: Path, schema: dict[str, Any], name: str, expected: type[Exception]
) -> None:
    scenario(cli, name)
    with pytest.raises(expected) as exc:
        run(cli, schema)
    assert str(exc.value) == expected.__name__ and exc.value.__cause__ is None
    trace = json.loads((cli.parent / "trace.json").read_text())
    assert not Path(trace["cwd"]).exists()


def test_limit_parses_only_unambiguous_timestamp(cli: Path, schema: dict[str, Any]) -> None:
    scenario(cli, "limit_iso")
    with pytest.raises(ExecutorLimitReached) as exc:
        run(cli, schema)
    assert exc.value.resume_at == datetime(2026, 10, 6, 9, tzinfo=UTC)
    scenario(cli, "limit")
    with pytest.raises(ExecutorLimitReached) as exc:
        run(cli, schema)
    assert exc.value.resume_at is None


def test_timeout_kills_parent_and_descendant_group(cli: Path, schema: dict[str, Any]) -> None:
    scenario(cli, "slow")
    started = time.monotonic()
    with pytest.raises(ExecutorTimeout):
        run(cli, schema, timeout=1)
    assert time.monotonic() - started < 3
    child_pid = int((cli.parent / "child.pid").read_text())
    # A killed grandchild may briefly be a zombie awaiting the OS reaper; it cannot run.
    state = subprocess.run(  # noqa: S603 - fixed ps command and integer fixture PID
        ["/bin/ps", "-o", "stat=", "-p", str(child_pid)],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    assert not state or state.startswith("Z")
    trace = json.loads((cli.parent / "trace.json").read_text())
    assert not Path(trace["cwd"]).exists()


def test_large_stdin_is_sent_completely_without_a_packet_file(
    cli: Path, schema: dict[str, Any]
) -> None:
    packet = "hostile synthetic data " * 10_000
    ClaudeCodeExecutor(cli).run("prompt", packet, schema, timeout_seconds=5)
    trace = json.loads((cli.parent / "trace.json").read_text())
    assert trace["stdin"] == "prompt\n\n" + packet and trace["files"] == []


@pytest.mark.parametrize(
    "logged_in,expected", [(True, True), (False, False), ("true", None), (None, None)]
)
def test_detect_is_defensive_and_sends_no_prompt(
    cli: Path, logged_in: Any, expected: bool | None
) -> None:
    scenario(cli, "success", loggedIn=logged_in, auth_code=0 if logged_in else 1)
    status = ClaudeCodeExecutor(cli).detect()
    assert status.installed and status.version == "2.1.289" and status.signed_in is expected
    trace = json.loads((cli.parent / "trace.json").read_text())
    assert trace["argv"] == ["auth", "status"] and trace["stdin"] == ""
    assert set(trace["environment"]) - {"__CF_USER_TEXT_ENCODING"} == {
        "HOME",
        "PATH",
        "USER",
        "TMPDIR",
        "LANG",
    }


def test_missing_binary_is_not_installed_and_detection_does_not_raise(tmp_path: Path) -> None:
    executor = ClaudeCodeExecutor(tmp_path / "missing")
    assert not executor.detect().installed
    with pytest.raises(ExecutorNotInstalled):
        executor.run("prompt", "packet", {}, timeout_seconds=1)


def test_lookup_prefers_home_binary_over_path(cli: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = cli.parent / "home"
    preferred = home / ".local/bin/claude"
    preferred.parent.mkdir(parents=True)
    preferred.write_text("fixture")
    preferred.chmod(0o700)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", str(cli.parent))
    assert _binary(None) == preferred and _binary(cli) == cli


def test_success_text_with_login_words_is_data_not_a_failure(
    cli: Path, schema: dict[str, Any]
) -> None:
    scenario(
        cli, "success", output={"results": []}, usage={"input_tokens": True, "output_tokens": -1}
    )
    result = run(cli, schema)
    assert result.input_tokens is None and result.output_tokens is None


def test_contract_has_every_item_property_required_and_removes_metadata(
    cli: Path, schema: dict[str, Any]
) -> None:
    properties = schema["properties"]["results"]["items"]["properties"]["items"]["items"]
    assert set(properties["required"]) == set(properties["properties"])
    with_metadata = {**schema, "$schema": "fixture", "$id": "fixture"}
    run(cli, with_metadata)
    argv = json.loads((cli.parent / "trace.json").read_text())["argv"]
    assert json.loads(argv[-1]) == schema


def test_custom_model_is_one_argument(cli: Path, schema: dict[str, Any]) -> None:
    ClaudeCodeExecutor(cli, model="haiku-fixture").run("prompt", "packet", schema)
    argv = json.loads((cli.parent / "trace.json").read_text())["argv"]
    assert argv[argv.index("--model") + 1] == "haiku-fixture"


@pytest.mark.parametrize("auth_raw,code", [("not json", 0), ('{"loggedIn":true}', 1)])
def test_detect_invalid_or_failed_auth_is_unknown(cli: Path, auth_raw: str, code: int) -> None:
    scenario(cli, "success", auth_raw=auth_raw, auth_code=code, version="unrecognized")
    status = ClaudeCodeExecutor(cli).detect()
    assert status.installed and status.version is None and status.signed_in is None


def test_deadline_applies_while_child_refuses_stdin(cli: Path, schema: dict[str, Any]) -> None:
    scenario(cli, "no_read")
    with pytest.raises(ExecutorTimeout):
        ClaudeCodeExecutor(cli).run("prompt", "x" * 200_000, schema, timeout_seconds=0.5)


def test_no_input_output_or_secret_in_logs(
    cli: Path, schema: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    with caplog.at_level(logging.INFO, logger="firra.ai.claude_code"):
        run(cli, schema)
    assert "ai.claude_finished" in caplog.text
    assert not any(
        marker in caplog.text
        for marker in (
            "synthetic prompt",
            "synthetic packet",
            "structured_output",
            "input_tokens",
            "results",
        )
    )
