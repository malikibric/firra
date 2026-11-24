"""Run each existing transactional pgTAP file through psql and validate its TAP plan."""

import os
import re
import subprocess
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    binary = Path(os.environ["FIRRA_LOCAL_PG_BIN"]) / "psql"
    database = os.environ["FIRRA_DATABASE_URL"]
    files = sorted((root / "supabase/tests").glob("*.test.sql"))
    failed = assertions = successful = 0
    for path in files:
        result = subprocess.run(  # noqa: S603 — bundled binary, checked-in synthetic tests
            [  # nosemgrep
                str(binary),
                "-X",
                "-qAt",
                "-v",
                "ON_ERROR_STOP=1",
                "-d",
                database,
                "-f",
                str(path),
            ],  # nosemgrep
            capture_output=True,
            text=True,
            timeout=120,
            # Supabase keeps extensions (pgTAP, pgvector) in schema `extensions` and
            # puts it on the search path.
            env={
                "PATH": "/usr/bin:/bin",
                "LC_ALL": "en_US.UTF-8",
                "PGOPTIONS": "-c search_path=public,extensions",
            },
        )
        plans = re.findall(r"^1\.\.(\d+)(?:\s.*)?$", result.stdout, re.MULTILINE)
        tests = re.findall(r"^(not ok|ok)\s+(\d+)([^\n]*)", result.stdout, re.MULTILINE)
        broken = [f"{number}{name}" for status, number, name in tests if status == "not ok"]
        valid = (
            result.returncode == 0
            and len(plans) == 1
            and int(plans[0]) == len(tests)
            and [int(number) for _, number, _ in tests] == list(range(1, len(tests) + 1))
            and not broken
            and "Bail out!" not in result.stdout
        )
        assertions += len(tests)
        successful += sum(status == "ok" for status, _, _ in tests)
        if valid:
            print(f"PASS {path.name}: {len(tests)} assertions")  # noqa: T201
        else:
            failed += 1
            print(f"FAIL {path.name}: {len(tests)} assertions; {broken or 'SQL/plan error'}")  # noqa: T201
            print(result.stderr.strip())  # noqa: T201 — synthetic test diagnostics
    print(  # noqa: T201
        f"pgTAP: {len(files) - failed} files passed, {failed} failed; "
        f"{successful}/{assertions} assertions passed"
    )
    return int(failed > 0)


if __name__ == "__main__":
    raise SystemExit(main())
