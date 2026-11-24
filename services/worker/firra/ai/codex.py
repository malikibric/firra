"""Codex CLI executor, refused unless the owner accepts what the CLI cannot prevent.

Docs checked: docs/research/ai-cli.md and docs/research/ai-cli-probes.md (codex-cli 0.160.0).
Claude Code can run with no tools at all (``--tools ""``). Codex has **no switch to disable
tools**: ``-s read-only`` blocks writes, but the model may still read files the owner's account
can read, and the probes proved only the model's behaviour against one hostile packet. So the
sandbox standard of ``claude_code.py`` cannot be met, and ``run`` raises before it spawns
anything unless ``accept_read_only_sandbox`` is set. Nothing wires that flag yet: the AI
runtime still answers ``codex_not_available``.

Everything else follows ``claude_code.py``: empty private temp cwd, minimal environment, no user
config or rules, no session persistence, packet on stdin only, bounded output, process group
killed. The schema and the answer file live in a second private temp directory that holds no
client text and is removed afterwards.
"""

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from firra.ai.claude_code import MAX_CAPTURE_BYTES, _binary, _failure, _invoke
from firra.ai.executor import ExecutorFailed, ExecutorKind, ExecutorResult


def _answer(path: Path) -> dict[str, Any]:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_CAPTURE_BYTES:
                raise ExecutorFailed()
            value = json.loads(handle.read())
    except (OSError, ValueError, UnicodeError):
        raise ExecutorFailed() from None
    if not isinstance(value, dict):
        raise ExecutorFailed()
    return value


class CodexExecutor:
    kind = ExecutorKind.codex

    def __init__(
        self, binary: Path | None = None, *, accept_read_only_sandbox: bool = False
    ) -> None:
        self.binary = binary
        self.accept_read_only_sandbox = accept_read_only_sandbox

    def run(
        self, prompt: str, packet_text: str, schema: dict[str, Any], *, timeout_seconds: float = 300
    ) -> ExecutorResult:
        if not self.accept_read_only_sandbox:
            raise ExecutorFailed()
        contract = {key: value for key, value in schema.items() if key not in ("$schema", "$id")}
        with tempfile.TemporaryDirectory(prefix="firra-codex-io-") as root:
            Path(root).chmod(0o700)
            schema_file, answer_file = Path(root) / "schema.json", Path(root) / "last.json"
            schema_file.write_text(json.dumps(contract, separators=(",", ":")))
            schema_file.chmod(0o600)
            args = [
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
                str(schema_file),
                "-o",
                str(answer_file),
                "-",
            ]
            code, _, stderr = _invoke(
                _binary(self.binary, "codex"),
                args,
                f"{prompt}\n\n{packet_text}",
                timeout_seconds=timeout_seconds,
                tool="codex",
            )
            if code != 0:
                _failure(stderr.decode("utf-8", errors="replace"))
            value = _answer(answer_file)
        return ExecutorResult(raw=json.dumps(value, separators=(",", ":")))
