"""Synthetic CLI; scenario.json and trace.json live beside the copied executable."""
# ruff: noqa: T201 - this fake CLI's protocol is stdout

import json
import os
import subprocess
import sys
import time
from pathlib import Path

root = Path(__file__).resolve().parent
scenario = json.loads((root / "scenario.json").read_text())
if scenario.get("name") == "no_read":
    time.sleep(60)
text = sys.stdin.read()
(root / "trace.json").write_text(
    json.dumps(
        {
            "argv": sys.argv[1:],
            "cwd": str(Path.cwd()),
            "files": [path.name for path in Path.cwd().iterdir()],
            "environment": dict(os.environ),
            "stdin": text,
            "mode": Path.cwd().stat().st_mode & 0o777,
        }
    )
)
if sys.argv[1:] == ["--version"]:
    print(scenario.get("version", "2.1.289 (Claude Code)"))
    sys.exit(0)
if sys.argv[1:] == ["auth", "status"]:
    print(scenario.get("auth_raw", json.dumps({"loggedIn": scenario.get("loggedIn", True)})))
    sys.exit(scenario.get("auth_code", 0))

name = scenario.get("name", "success")
if name == "slow":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    (root / "child.pid").write_text(str(child.pid))
    time.sleep(60)
elif name.startswith("huge"):
    pipe = sys.stderr.buffer if name == "huge_stderr" else sys.stdout.buffer
    for _ in range(1024):
        pipe.write(b"x" * 8192)
        pipe.flush()
elif name == "malformed":
    print("not json")
elif name == "exit":
    print("fixture failure", file=sys.stderr)
    sys.exit(7)
elif name in ("logged_out", "limit", "limit_iso", "failure"):
    messages = {
        "logged_out": "Not logged in · Please run /login",
        "limit": "Usage limit reached, resets later",
        "limit_iso": "Usage limit reached, resets 2026-10-06T09:00:00Z",
        "failure": "fixture failure",
    }
    print(json.dumps({"is_error": True, "result": messages[name]}))
else:
    value = scenario.get("output", {"results": [{"record": "r1", "items": []}]})
    envelope = {
        "is_error": False,
        "modelUsage": {"claude-haiku-fixture": {}},
        "usage": scenario.get("usage", {"input_tokens": 100, "output_tokens": 20}),
    }
    envelope["result" if name == "result" else "structured_output"] = (
        json.dumps(value) if name == "result" else value
    )
    print(json.dumps(envelope))
