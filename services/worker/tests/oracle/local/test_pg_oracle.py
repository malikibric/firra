"""Independent oracle for the Postgres supervisor (L1-08, requirements 1-9).

Written from the L1-02 brief and ``docs/plans/local-v1.md`` T2/L1-02, not from
``firra/local/pg.py``. Names and signatures below are looked up only to drive
the cluster; every assertion restates the spec. Needs the bundled binaries::

    FIRRA_LOCAL_PG_BIN=~/firra-tools/engine/postgres/bin uv run pytest \
        services/worker/tests/oracle/local -m integration -q

Skipped cleanly without the binaries.
"""

import contextlib
import os
import shutil
import signal
import stat
import subprocess
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

from firra.local.pg import PostgresError, PostgresSupervisor, PostgresVersionError

pytestmark = pytest.mark.integration

PG_BIN = os.environ.get("FIRRA_LOCAL_PG_BIN")
MARKER = "oracle_marker_pg_7f3a9c"


def _bin() -> Path:
    if not PG_BIN or not (Path(PG_BIN) / "initdb").exists():
        pytest.skip("FIRRA_LOCAL_PG_BIN is not set")
    return Path(PG_BIN)


@pytest.fixture
def root() -> Iterator[Path]:
    path = Path(tempfile.mkdtemp(prefix="firra-oracle-", dir="/tmp"))
    yield path
    _force_stop(path)
    for child in sorted(path.rglob("*"), reverse=True):
        with contextlib.suppress(OSError):
            if child.is_symlink() or child.is_file():
                child.unlink()
            else:
                child.rmdir()
    with contextlib.suppress(OSError):
        path.rmdir()


def _force_stop(root: Path) -> None:
    if PG_BIN is None:
        return
    data = root / "data"
    if (data / "PG_VERSION").exists():
        subprocess.run(  # noqa: S603
            [f"{PG_BIN}/pg_ctl", "-D", str(data), "-m", "immediate", "-w", "stop"],
            capture_output=True,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "en_US.UTF-8"},
        )


@pytest.fixture
def supervisor(root: Path) -> PostgresSupervisor:
    _bin()
    return PostgresSupervisor(Path(PG_BIN or ""), root / "data", root / "run")


@pytest.fixture
def running(supervisor: PostgresSupervisor) -> Iterator[PostgresSupervisor]:
    supervisor.ensure_cluster()
    supervisor.start()
    yield supervisor
    supervisor.stop()


def _postmaster_pid(supervisor: PostgresSupervisor) -> int:
    return int((supervisor.data_dir / "postmaster.pid").read_text().splitlines()[0])


def _postgres_pids() -> set[int]:
    """All postgres pids on the machine (informational only: other servers may run here)."""
    out = subprocess.run(["/bin/ps", "-ax", "-o", "pid,comm"], capture_output=True, text=True)
    pids = set()
    for line in out.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) == 2 and parts[1].split("/")[-1] == "postgres" and parts[0].isdigit():
            pids.add(int(parts[0]))
    return pids


def _our_postgres_pids(supervisor: PostgresSupervisor) -> set[int]:
    """Only this cluster: the postmaster from postmaster.pid plus its process group."""
    postmaster = _postmaster_pid(supervisor)
    out = subprocess.run(["/bin/ps", "-ax", "-o", "pid,pgid,comm"], capture_output=True, text=True)
    ours = set()
    for line in out.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) == 3 and parts[0].isdigit() and parts[2].split("/")[-1] == "postgres":
            pid, pgid = int(parts[0]), int(parts[1])
            if pid == postmaster or pgid == postmaster:
                ours.add(pid)
    return ours


def _fails(conn: psycopg.Connection, sql: str) -> bool:
    """Run SQL that must fail (oracle markers are test constants, never input)."""
    try:
        conn.execute(sql)
    except psycopg.Error:
        return True
    return False


def _tcp_listeners(pid: int) -> str:
    lsof = shutil.which("lsof")
    if lsof is None:
        pytest.skip("lsof is not installed")
    out = subprocess.run(  # noqa: S603
        [lsof, "-nP", "-a", "-p", str(pid), "-iTCP", "-sTCP:LISTEN"],
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


def test_1_no_tcp_listeners_postmaster_or_children(running: PostgresSupervisor) -> None:
    """Spec: our cluster never listens on TCP (other servers on this Mac are out of scope)."""
    pids = _our_postgres_pids(running)
    assert pids, "expected our postgres processes after start"
    for pid in sorted(pids):
        assert _tcp_listeners(pid) == "", f"pid {pid} listens on TCP"
    with psycopg.connect(running.conninfo) as conn:
        row = conn.execute("show listen_addresses").fetchone()
        assert row is not None and row[0] == ""


def test_2_private_modes(running: PostgresSupervisor, root: Path) -> None:
    """Spec: data, run and log directories 0700; socket not group/other accessible."""
    for directory in (running.data_dir, running.run_dir, running.log_file.parent):
        mode = stat.S_IMODE(directory.stat().st_mode)
        assert mode == 0o700, f"{directory} has mode {oct(mode)}"
    assert (running.run_dir / ".s.PGSQL.5432").exists()
    for path in running.data_dir.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0, f"{path} is group/other accessible"


def test_3_stale_pid_of_live_process_is_not_killed(supervisor: PostgresSupervisor) -> None:
    """Spec: a postmaster.pid naming a live unrelated process must not kill it."""
    supervisor.ensure_cluster()
    with subprocess.Popen(["/bin/sleep", "60"]) as child:
        assert child.pid is not None
        pid_file = supervisor.data_dir / "postmaster.pid"
        pid_file.write_text(f"{child.pid}\n")
        with contextlib.suppress(PostgresError):  # refusing is fine; killing is not
            supervisor.start()
        try:
            assert child.poll() is None, "start() killed the unrelated process"
            assert pid_file.read_text().splitlines()[0] == str(child.pid)
        finally:
            child.terminate()
    pid_file.unlink(missing_ok=True)
    supervisor.stop()


def test_3_stale_pid_of_dead_process_starts(supervisor: PostgresSupervisor) -> None:
    """Spec: a stale pid of a dead process must not block start()."""
    supervisor.ensure_cluster()
    with subprocess.Popen(["true"]) as child:  # noqa: S607
        dead = child.pid
    assert dead is not None
    (supervisor.data_dir / "postmaster.pid").write_text(f"{dead}\n")
    supervisor.start()
    try:
        assert supervisor.is_running()
    finally:
        supervisor.stop()


def test_4_crash_keeps_committed_loses_uncommitted(running: PostgresSupervisor) -> None:
    """Spec: kill -9 during a write; committed rows survive, uncommitted do not."""
    old_pids = _our_postgres_pids(running)
    assert old_pids, "expected our postgres processes before the crash"
    with psycopg.connect(running.conninfo, autocommit=True) as conn:
        conn.execute("create table oracle_crash(id int primary key, v text)")
        conn.execute("insert into oracle_crash values (1, 'committed')")
    holder = psycopg.connect(running.conninfo, autocommit=False)
    holder.execute("insert into oracle_crash values (2, 'uncommitted')")
    os.kill(_postmaster_pid(running), signal.SIGKILL)
    with contextlib.suppress(Exception):
        holder.close()
    running.start()
    try:
        with psycopg.connect(running.conninfo) as conn:
            rows = {r[0]: r[1] for r in conn.execute("select id, v from oracle_crash")}
        assert rows.get(1) == "committed"
        assert 2 not in rows
        for pid in old_pids:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            else:
                name = subprocess.run(  # noqa: S603
                    ["/bin/ps", "-o", "comm=", "-p", str(pid)], capture_output=True, text=True
                ).stdout.strip()
                assert Path(name).name != "postgres", f"old postgres pid {pid} survived"
    finally:
        running.stop()


def test_5_other_major_is_refused_and_untouched(supervisor: PostgresSupervisor) -> None:
    """Spec: another major is refused and the directory is left unmodified."""
    supervisor.ensure_cluster()
    (supervisor.data_dir / "PG_VERSION").write_text("15\n")
    before = sorted(
        (p.name, p.stat().st_size) for p in supervisor.data_dir.iterdir() if p.is_file()
    )
    with pytest.raises(PostgresVersionError):
        supervisor.ensure_cluster()
    after = sorted((p.name, p.stat().st_size) for p in supervisor.data_dir.iterdir() if p.is_file())
    assert before == after


def test_6_symlinked_data_dir_is_refused(root: Path) -> None:
    """Spec: a symlinked data directory must be refused."""
    _bin()
    real = root / "real-data"
    real.mkdir(mode=0o700)
    link = root / "link-data"
    link.symlink_to(real)
    supervisor = PostgresSupervisor(Path(PG_BIN or ""), link, root / "run")
    with pytest.raises(PostgresError):
        supervisor.ensure_cluster()


def test_6_long_socket_path_falls_back(root: Path) -> None:
    """Spec: socket paths over 100 bytes fall back to /tmp/firra-<uid>, mode 0700."""
    _bin()
    long_run = root / ("x" * 90)
    supervisor = PostgresSupervisor(Path(PG_BIN or ""), root / "data", long_run)
    assert len(os.fsencode(long_run / ".s.PGSQL.5432")) > 100
    assert supervisor.run_dir == Path(f"/tmp/firra-{os.getuid()}")  # noqa: S108
    created = not supervisor.run_dir.exists()
    try:
        supervisor.ensure_cluster()
        assert stat.S_IMODE(supervisor.run_dir.stat().st_mode) == 0o700
    finally:
        if created:
            with contextlib.suppress(OSError):
                supervisor.run_dir.rmdir()


def test_6_unwritable_run_parent_is_refused(root: Path) -> None:
    """Spec: a run directory that cannot be created must be refused (any error, recorded)."""
    _bin()
    parent = root / "locked"
    parent.mkdir(mode=0o500)
    supervisor = PostgresSupervisor(Path(PG_BIN or ""), root / "data", parent / "run")
    try:
        with pytest.raises((OSError, PostgresError)):
            supervisor.ensure_cluster()
    finally:
        parent.chmod(0o700)


def _server_log_text(running: PostgresSupervisor) -> str:
    text = ""
    for directory in (
        running.log_file.parent,
        running.data_dir / "log",
        running.data_dir / "pg_log",
    ):
        if directory.is_dir():
            for log_file in directory.iterdir():
                if log_file.is_file():
                    text += log_file.read_text(errors="replace")
    return text


def test_7_failing_statement_text_never_reaches_logs(running: PostgresSupervisor) -> None:
    """Spec (L1-02 req 3): log_min_error_statement=panic; statement text is never logged."""
    with psycopg.connect(running.conninfo, autocommit=True) as conn:
        assert _fails(conn, f"select 1/0 /* {MARKER}_stmt */")
    time.sleep(0.5)
    assert f"{MARKER}_stmt" not in _server_log_text(running)


def test_7_error_message_content_reaches_logs(running: PostgresSupervisor) -> None:
    """Beyond the written contract: error MESSAGE text (literal values, RAISE text)
    is still logged, e.g. ``invalid input syntax for type integer: "<value>"``.
    log_min_error_statement hides statements, not messages. See Report (spec gap)."""
    markers = [f"{MARKER}_{i}" for i in range(1, 4)]
    with psycopg.connect(running.conninfo, autocommit=True) as conn:
        conn.execute("create table oracle_log(id int primary key)")
        # Oracle markers are test constants, never user input.
        assert _fails(conn, f"insert into oracle_log values ('{markers[0]}')")  # noqa: S608
        assert _fails(conn, f"insert into oracle_log values ('{markers[1]}')")  # noqa: S608
        assert _fails(conn, f"do $$ begin raise exception '{markers[2]}'; end $$")
    time.sleep(0.5)
    log_text = _server_log_text(running)
    for marker in markers:
        assert marker not in log_text, f"marker {marker} leaked into server logs"


def test_8_postgres_environment_has_no_parent_secrets(
    running: PostgresSupervisor, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec: minimal environment; planted parent variables must not reach postgres."""
    planted = {
        "PGPASSWORD": "oracle-secret-pgpassword",
        "FIRRA_SECRET_TEST": "oracle-secret-firra",
        "AWS_SECRET_ACCESS_KEY": "oracle-secret-aws",
        "ANTHROPIC_API_KEY": "oracle-secret-anthropic",
    }
    for key, value in planted.items():
        monkeypatch.setenv(key, value)
    pid = _postmaster_pid(running)
    out = subprocess.run(["ps", "eww", "-p", str(pid)], capture_output=True, text=True)  # noqa: S603,S607
    for value in planted.values():
        assert value not in out.stdout, f"parent secret reached the postgres environment: {value}"


def test_9_locale_and_collation(running: PostgresSupervisor) -> None:
    """Spec: cluster locale en_US.UTF-8; text matching depends on it."""
    with psycopg.connect(running.conninfo) as conn:
        lower = conn.execute("select lower('ÄÖÜ')").fetchone()
        assert lower is not None and lower[0] == "äöü"
        ilike = conn.execute("select 'ABC' ilike 'abc'").fetchone()
        assert ilike is not None and ilike[0] is True
