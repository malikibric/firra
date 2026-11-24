"""Locked-down Claude Code CLI, using the owner's subscription login.

Docs checked: https://code.claude.com/docs/en/cli-reference (2026-10-05), and
docs/research/ai-cli-probes.md (Claude 2.1.289). The task's probed command is
authoritative: no bare mode, tools, MCP, settings, or session persistence.
Packets travel only on stdin; captured output stays bounded and in memory.
"""

import json
import os
import pwd
import re
import selectors
import shutil
import signal
import subprocess
import tempfile
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from firra.ai.executor import (
    ExecutorFailed,
    ExecutorKind,
    ExecutorLimitReached,
    ExecutorNotInstalled,
    ExecutorNotSignedIn,
    ExecutorResult,
    ExecutorTimeout,
)
from firra.logging import get_logger
from firra_contracts.generated.local.engine_status import Tool as ToolStatus

log = get_logger(__name__)
MAX_CAPTURE_BYTES = 4 * 1024 * 1024


def _environment() -> dict[str, str]:
    home = str(Path.home())
    return {
        "HOME": home,
        "PATH": f"/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin:{home}/.local/bin",
        "USER": pwd.getpwuid(os.getuid()).pw_name,
        "TMPDIR": os.environ.get("TMPDIR") or tempfile.gettempdir(),
        "LANG": "en_US.UTF-8",
    }


def _binary(explicit: Path | None, name: str = "claude") -> Path:
    if explicit is not None:
        candidates = [explicit.expanduser().absolute()]
    else:
        found = shutil.which(name)
        candidates = [
            Path.home() / ".local/bin" / name,
            Path("/opt/homebrew/bin") / name,
            Path("/usr/local/bin") / name,
        ]
        if found:
            candidates.append(Path(found).absolute())
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            return path
    raise ExecutorNotInstalled()


def _kill_group(process: subprocess.Popen[bytes]) -> None:
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()


def _invoke(
    binary: Path, args: list[str], text: str, *, timeout_seconds: float, tool: str = "claude"
) -> tuple[int, bytes, bytes]:
    """Drain both output pipes while writing stdin; cap their combined bytes.

    communicate() would allocate arbitrary output before a cap could be checked.
    Nonblocking pipes also enforce the deadline if a child never reads its input.
    """
    if timeout_seconds <= 0:
        raise ExecutorTimeout()
    started = time.monotonic()
    process: subprocess.Popen[bytes] | None = None
    with tempfile.TemporaryDirectory(prefix=f"firra-{tool}-") as root:
        Path(root).chmod(0o700)
        try:
            process = subprocess.Popen(  # noqa: S603 - fixed flags, resolved executable, no shell
                [str(binary), *args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=root,
                env=_environment(),
                start_new_session=True,
                bufsize=0,
            )
            if process.stdin is None or process.stdout is None or process.stderr is None:
                raise ExecutorFailed()  # PIPE above always supplies these streams.
            data = text.encode("utf-8")
            offset = 0
            output = {"stdout": bytearray(), "stderr": bytearray()}
            captured = 0
            with selectors.DefaultSelector() as selector:
                for pipe, name in ((process.stdout, "stdout"), (process.stderr, "stderr")):
                    os.set_blocking(pipe.fileno(), False)
                    selector.register(pipe, selectors.EVENT_READ, name)
                os.set_blocking(process.stdin.fileno(), False)
                if data:
                    selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
                else:
                    process.stdin.close()
                while selector.get_map():
                    remaining = timeout_seconds - (time.monotonic() - started)
                    if remaining <= 0:
                        raise ExecutorTimeout()
                    for key, _ in selector.select(remaining):
                        if key.data == "stdin":
                            try:
                                offset += os.write(key.fd, data[offset : offset + 8192])
                            except BrokenPipeError:
                                offset = len(data)
                            if offset == len(data):
                                selector.unregister(key.fileobj)
                                process.stdin.close()
                        else:
                            chunk = os.read(key.fd, 8192)
                            if not chunk:
                                selector.unregister(key.fileobj)
                                continue
                            captured += len(chunk)
                            if captured > MAX_CAPTURE_BYTES:
                                raise ExecutorFailed()
                            output[key.data].extend(chunk)
            try:
                code = process.wait(timeout=max(0, timeout_seconds - (time.monotonic() - started)))
            except subprocess.TimeoutExpired:
                raise ExecutorTimeout() from None
            return code, bytes(output["stdout"]), bytes(output["stderr"])
        except FileNotFoundError:
            raise ExecutorNotInstalled() from None
        except OSError:
            raise ExecutorFailed() from None
        finally:
            if process is not None:
                # Also kill descendants after the parent exits; inherited pipes must not
                # leave an orphan beyond either a normal return or the caller's deadline.
                _kill_group(process)
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()
            log.info(
                f"ai.{tool}_finished",
                duration_ms=round((time.monotonic() - started) * 1000),
                exit_code=process.returncode if process is not None else None,
            )


def _resume_at(text: str) -> datetime | None:
    match = re.search(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})", text)
    if not match:
        return None
    try:
        return datetime.fromisoformat(match[0].replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _failure(text: str) -> None:
    if re.search(r"not logged in|/login", text, re.IGNORECASE):
        raise ExecutorNotSignedIn()
    if re.search(r"\blimit\b|\bresets?\b|rate[ -]limit", text, re.IGNORECASE):
        raise ExecutorLimitReached(_resume_at(text))
    raise ExecutorFailed()


def _tokens(usage: dict[str, Any], name: str) -> int | None:
    value = usage.get(name)
    return value if type(value) is int and value >= 0 else None


class ClaudeCodeExecutor:
    kind = ExecutorKind.claude_code

    def __init__(self, binary: Path | None = None, *, model: str | None = None) -> None:
        self.binary = binary
        self.model = model or "haiku"

    def detect(self) -> ToolStatus:
        status: dict[str, Any] = {
            "tool": self.kind.value,
            "installed": False,
            "version": None,
            "signed_in": None,
        }
        try:
            binary = _binary(self.binary)
        except ExecutorNotInstalled:
            return ToolStatus.model_validate(status)
        status["installed"] = True
        try:
            code, raw, _ = _invoke(binary, ["--version"], "", timeout_seconds=10)
            version = re.search(rb"\b(2\.\d+\.\d+)\b", raw)
            if code == 0 and version:
                status["version"] = version[1].decode("ascii")
            code, raw, _ = _invoke(binary, ["auth", "status"], "", timeout_seconds=10)
            auth = json.loads(raw)
            if (
                isinstance(auth, dict)
                and type(auth.get("loggedIn")) is bool
                and (code == 0 or auth["loggedIn"] is False)
            ):
                status["signed_in"] = auth["loggedIn"]
        except (ExecutorFailed, ExecutorTimeout, ExecutorNotInstalled, ValueError, UnicodeError):
            pass  # Presence known, auth/version unknown; never guess successful login.
        return ToolStatus.model_validate(status)

    def run(
        self, prompt: str, packet_text: str, schema: dict[str, Any], *, timeout_seconds: float = 300
    ) -> ExecutorResult:
        # The caller supplies the contract JSON, not the generated Pydantic schema.
        contract = {key: value for key, value in schema.items() if key not in ("$schema", "$id")}
        args = [
            "-p",
            "--model",
            self.model,
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
            json.dumps(contract, separators=(",", ":")),
        ]
        code, stdout, stderr = _invoke(
            _binary(self.binary),
            args,
            f"{prompt}\n\n{packet_text}",
            timeout_seconds=timeout_seconds,
        )
        try:
            envelope = json.loads(stdout)
        except (ValueError, UnicodeError):
            if code:
                _failure(stderr.decode("utf-8", errors="replace"))
            raise ExecutorFailed() from None
        if not isinstance(envelope, dict):
            raise ExecutorFailed()
        if code != 0 or envelope.get("is_error") is True:
            result = envelope.get("result")
            _failure(
                (result if isinstance(result, str) else "")
                + "\n"
                + stderr.decode("utf-8", errors="replace")
            )
        value = envelope.get("structured_output")
        if value is None:
            try:
                value = json.loads(envelope["result"])
            except (KeyError, TypeError, ValueError):
                raise ExecutorFailed() from None
        if not isinstance(value, dict):
            raise ExecutorFailed()
        models = envelope.get("modelUsage")
        model = next(iter(models), None) if isinstance(models, dict) else None
        usage = envelope.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        return ExecutorResult(
            raw=json.dumps(value, separators=(",", ":")),
            model=model,
            input_tokens=_tokens(usage, "input_tokens"),
            output_tokens=_tokens(usage, "output_tokens"),
        )
