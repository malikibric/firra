"""Codex has no tool switch, so the executor refuses by default; when accepted, it is as tight
as the CLI allows. Every case here fails if a restriction is dropped."""

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

import firra
from firra.ai.codex import CodexExecutor
from firra.ai.executor import (
    ExecutorFailed,
    ExecutorKind,
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
    source = Path(__file__).resolve().parents[2] / "fixtures/fake_codex.py"
    binary = tmp_path / "codex"
    binary.write_text(f"#!{sys.executable}\n" + source.read_text())
    binary.chmod(0o700)
    scenario(binary, "success")
    return binary


def scenario(binary: Path, name: str, **kwargs: Any) -> None:
    (binary.parent / "scenario.json").write_text(json.dumps({"name": name, **kwargs}))


def run(binary: Path, schema: dict[str, Any], *, timeout: float = 5) -> Any:
    return CodexExecutor(binary, accept_read_only_sandbox=True).run(
        "synthetic prompt", "synthetic packet", schema, timeout_seconds=timeout
    )


def trace(binary: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((binary.parent / "trace.json").read_text())
    return loaded


def test_refuses_by_default_before_spawning_anything(cli: Path, schema: dict[str, Any]) -> None:
    assert CodexExecutor(cli).accept_read_only_sandbox is False
    with pytest.raises(ExecutorFailed):
        CodexExecutor(cli).run("prompt", "packet", schema, timeout_seconds=5)
    assert not (cli.parent / "trace.json").exists()


def test_nothing_in_the_engine_turns_the_acceptance_on() -> None:
    """The acceptance is a decision for the owner and L3-03's wiring; no engine code makes it."""
    package = Path(firra.__file__).parent
    offenders = [
        str(path)
        for path in package.rglob("*.py")
        if "accept_read_only_sandbox=True" in path.read_text()
    ]
    assert offenders == []


def test_success_returns_the_final_message_without_invented_usage(
    cli: Path, schema: dict[str, Any]
) -> None:
    result = run(cli, schema)
    parsed = AiExtractionOutput.model_validate_json(result.raw, strict=True)
    assert parsed.results[0].record == "r1"
    assert (result.model, result.input_tokens, result.output_tokens) == (None, None, None)
    assert CodexExecutor.kind is ExecutorKind.codex


def test_exact_argv_private_dirs_removed_clean_environment_and_stdin(
    cli: Path, schema: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("OPENAI_API_KEY", "CODEX_HOME", "ANTHROPIC_API_KEY", "FIRRA_SECRET_TEST"):
        monkeypatch.setenv(name, "fixture-secret-never-forward")
    passed: list[dict[str, str]] = []
    popen = subprocess.Popen

    def capture_environment(*args: Any, **kwargs: Any) -> Any:
        passed.append(dict(kwargs["env"]))
        return popen(*args, **kwargs)

    monkeypatch.setattr("firra.ai.claude_code.subprocess.Popen", capture_environment)
    run(cli, schema)
    seen = trace(cli)
    schema_path, answer_path = seen["argv"][10], seen["argv"][12]
    assert seen["argv"] == [
        "exec",
        "-s",
        "read-only",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--ephemeral",
        "-C",
        ".",
        "--output-schema",
        schema_path,
        "-o",
        answer_path,
        "-",
    ]
    assert seen["argv"][9] == "--output-schema" and seen["argv"][11] == "-o"
    assert set(passed[0]) == {"HOME", "PATH", "USER", "TMPDIR", "LANG"}
    assert set(seen["environment"]) - {"__CF_USER_TEXT_ENCODING"} == set(passed[0])
    assert "fixture-secret-never-forward" not in str(seen["environment"])
    # The process directory is empty and private; the contract (no client text) lives apart.
    assert seen["files"] == [] and seen["mode"] == 0o700
    assert seen["cwd"] != str(Path.cwd()) and not Path(seen["cwd"]).exists()
    assert seen["schema"] == schema and seen["schema_mode"] == 0o600
    assert seen["io_mode"] == 0o700 and seen["io_files"] == ["schema.json"]
    assert not Path(seen["io_dir"]).exists()
    assert seen["stdin"] == "synthetic prompt\n\nsynthetic packet"
    assert "synthetic packet" not in json.dumps(seen["argv"])


@pytest.mark.parametrize(
    "name,expected",
    [
        ("logged_out", ExecutorNotSignedIn),
        ("limit", ExecutorLimitReached),
        ("exit", ExecutorFailed),
        ("malformed", ExecutorFailed),
        ("not_object", ExecutorFailed),
        ("missing", ExecutorFailed),
        ("symlink", ExecutorFailed),
        ("huge_answer", ExecutorFailed),
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
    seen = trace(cli)
    assert not Path(seen["cwd"]).exists() and not Path(seen["io_dir"]).exists()


def test_timeout_kills_parent_and_descendant_group(cli: Path, schema: dict[str, Any]) -> None:
    scenario(cli, "slow")
    started = time.monotonic()
    with pytest.raises(ExecutorTimeout):
        run(cli, schema, timeout=1)
    assert time.monotonic() - started < 3
    child_pid = int((cli.parent / "child.pid").read_text())
    state = subprocess.run(  # noqa: S603 - fixed ps command and integer fixture PID
        ["/bin/ps", "-o", "stat=", "-p", str(child_pid)],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    assert not state or state.startswith("Z")
    assert not Path(trace(cli)["io_dir"]).exists()
    scenario(cli, "no_read")
    with pytest.raises(ExecutorTimeout):
        CodexExecutor(cli, accept_read_only_sandbox=True).run(
            "prompt", "x" * 200_000, schema, timeout_seconds=0.5
        )


def test_large_stdin_is_sent_completely_without_a_packet_file(
    cli: Path, schema: dict[str, Any]
) -> None:
    packet = "hostile synthetic data " * 10_000
    CodexExecutor(cli, accept_read_only_sandbox=True).run(
        "prompt", packet, schema, timeout_seconds=5
    )
    seen = trace(cli)
    assert seen["stdin"] == "prompt\n\n" + packet
    assert seen["files"] == [] and seen["io_files"] == ["schema.json"]


def test_missing_binary_is_not_installed(tmp_path: Path, schema: dict[str, Any]) -> None:
    with pytest.raises(ExecutorNotInstalled):
        CodexExecutor(tmp_path / "missing", accept_read_only_sandbox=True).run(
            "prompt", "packet", schema, timeout_seconds=5
        )


def test_no_input_output_or_secret_in_logs(
    cli: Path, schema: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    with caplog.at_level(logging.INFO, logger="firra.ai.claude_code"):
        run(cli, schema)
    assert "ai.codex_finished" in caplog.text
    assert not any(
        marker in caplog.text for marker in ("synthetic prompt", "synthetic packet", "results")
    )
