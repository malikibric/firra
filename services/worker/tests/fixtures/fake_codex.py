"""Synthetic Codex CLI; scenario.json and trace.json live beside the copied executable."""
# ruff: noqa: T201 - this fake CLI's protocol is stderr and files

import json
import os
import subprocess
import sys
import time
from pathlib import Path

root = Path(__file__).resolve().parent
scenario = json.loads((root / "scenario.json").read_text())
name = scenario.get("name", "success")
if name == "no_read":
    time.sleep(60)
argv = sys.argv[1:]
text = sys.stdin.read()
schema_path = Path(argv[argv.index("--output-schema") + 1])
answer_path = Path(argv[argv.index("-o") + 1])
(root / "trace.json").write_text(
    json.dumps(
        {
            "argv": argv,
            "cwd": str(Path.cwd()),
            "files": [path.name for path in Path.cwd().iterdir()],
            "environment": dict(os.environ),
            "stdin": text,
            "mode": Path.cwd().stat().st_mode & 0o777,
            "schema": json.loads(schema_path.read_text()),
            "schema_mode": schema_path.stat().st_mode & 0o777,
            "io_dir": str(schema_path.parent),
            "io_mode": schema_path.parent.stat().st_mode & 0o777,
            "io_files": sorted(path.name for path in schema_path.parent.iterdir()),
        }
    )
)
if name == "slow":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    (root / "child.pid").write_text(str(child.pid))
    time.sleep(60)
elif name == "huge_stderr":
    for _ in range(1024):
        sys.stderr.buffer.write(b"x" * 8192)
        sys.stderr.buffer.flush()
elif name == "huge_answer":
    answer_path.write_bytes(b"[" + b" " * (5 * 1024 * 1024) + b"]")
elif name == "exit":
    print("fixture failure", file=sys.stderr)
    sys.exit(7)
elif name == "logged_out":
    print("Not logged in. Please run /login", file=sys.stderr)
    sys.exit(1)
elif name == "limit":
    print("Usage limit reached, resets later", file=sys.stderr)
    sys.exit(1)
elif name == "malformed":
    answer_path.write_text("not json")
elif name == "not_object":
    answer_path.write_text("[1]")
elif name == "symlink":
    target = root / "elsewhere.json"
    target.write_text(json.dumps({"results": []}))
    answer_path.symlink_to(target)
elif name == "missing":
    pass
else:
    answer_path.write_text(
        json.dumps(scenario.get("output", {"results": [{"record": "r1", "items": []}]}))
    )
