"""Real private clusters; no remote database or customer data."""

import os
import signal
import stat
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

from firra.local.pg import PostgresSupervisor

pytestmark = pytest.mark.integration
PG_BIN = os.environ.get("FIRRA_LOCAL_PG_BIN")


@pytest.fixture
def supervisor() -> Iterator[PostgresSupervisor]:
    binary = PG_BIN
    if not binary:
        pytest.skip("FIRRA_LOCAL_PG_BIN is not set")
    with tempfile.TemporaryDirectory(prefix="firra-supervisor-", dir="/tmp") as root:
        pg = PostgresSupervisor(Path(binary), Path(root) / "data", Path(root) / "run with spaces")
        try:
            yield pg
        finally:
            pg.stop()


def test_lifecycle_privacy_and_recovery(supervisor: PostgresSupervisor) -> None:
    pg = supervisor
    conninfo = pg.ensure_ready()
    with psycopg.connect(conninfo, autocommit=True) as conn:
        assert conn.execute("select to_regclass('public.client')").fetchone() == ("client",)
        conn.execute("create table firra_local.supervisor_fixture (value integer)")
        conn.execute("insert into firra_local.supervisor_fixture values (42)")
        with pytest.raises(psycopg.errors.DivisionByZero):
            conn.execute("select 1/0 /* SUPERVISOR_PRIVATE_MARKER */")
    assert "SUPERVISOR_PRIVATE_MARKER" not in pg.log_file.read_text()
    assert pg.ensure_ready() == conninfo
    pid = int((pg.data_dir / "postmaster.pid").read_text().splitlines()[0])
    children = subprocess.run(  # noqa: S603 — synthetic cluster pid
        ["/usr/bin/pgrep", "-P", str(pid)], capture_output=True, text=True, check=False, timeout=5
    ).stdout.split()
    for process in [str(pid), *children]:
        result = subprocess.run(  # noqa: S603 — synthetic cluster pid
            ["/usr/sbin/lsof", "-nP", "-a", "-p", process, "-iTCP", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        assert result.returncode == 1 and not result.stdout and not result.stderr
    for path in [pg.data_dir, pg.run_dir, pg.log_file.parent, pg.run_dir / ".s.PGSQL.5432"]:
        assert stat.S_IMODE(path.stat().st_mode) == 0o700
    print(  # noqa: T201 — acceptance evidence, synthetic state only
        "lsof_tcp_listeners=(empty); modes=data:0700 run:0700 log:0700 socket:0700; "
        "log_marker=absent"
    )
    pg.stop()
    pg.start()
    pid = int((pg.data_dir / "postmaster.pid").read_text().splitlines()[0])
    os.kill(pid, signal.SIGKILL)
    # Recover in a fresh supervisor immediately, without the old object's group state.
    pg = PostgresSupervisor(pg.bin_dir, pg.data_dir, pg.run_dir)
    pg.start()
    with pytest.raises(ProcessLookupError):
        os.killpg(pid, 0)
    with psycopg.connect(conninfo) as conn:
        assert conn.execute("select value from firra_local.supervisor_fixture").fetchone() == (42,)
    pg.stop()
    assert not pg.is_running()
    assert not (pg.run_dir / ".s.PGSQL.5432").exists()
