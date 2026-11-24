"""Private bundled PostgreSQL lifecycle; no inherited provider or database environment.

Docs checked: PostgreSQL 17 pg_ctl and runtime-config-logging, 2026-10-05.
"""

import contextlib
import os
import re
import shlex
import signal
import stat
import subprocess
import time
from pathlib import Path

import psycopg
from psycopg.conninfo import make_conninfo

from firra.db.local_cluster import ensure_database
from firra.local.db import migrate
from firra.logging import get_logger

log = get_logger(__name__)
ENV = {"PATH": "/usr/bin:/bin", "LC_ALL": "en_US.UTF-8"}


class PostgresError(RuntimeError):
    """A local lifecycle operation failed; subprocess output is deliberately omitted."""


class PostgresVersionError(PostgresError):
    """An existing cluster needs the explicit upgrade path."""


def _private_dir(path: Path) -> None:
    try:
        _check_private_dir(path)
    except OSError:
        raise PostgresError("directory is not usable") from None


def _check_private_dir(path: Path) -> None:
    if path.is_symlink():
        raise PostgresError("directory is a symlink")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise PostgresError("directory is not owned by this user")
    path.chmod(0o700)


class PostgresSupervisor:
    def __init__(self, bin_dir: Path, data_dir: Path, run_dir: Path | None = None) -> None:
        self.bin_dir = bin_dir.absolute()
        self.data_dir = data_dir.absolute()
        requested = run_dir or Path.home() / "Library/Application Support/Firra/run"
        self.run_dir = requested.absolute()
        if len(os.fsencode(self.run_dir / ".s.PGSQL.5432")) > 100:
            self.run_dir = Path(f"/tmp/firra-{os.getuid()}")  # noqa: S108 — ownership checked
        self.log_file = self.data_dir.parent / "log/postgres.log"
        self._group: int | None = None

    @property
    def conninfo(self) -> str:
        return make_conninfo(host=str(self.run_dir), user="postgres", dbname="firra")

    def _run(
        self, action: str, args: list[str], *, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        started = time.monotonic()
        code = -1
        try:
            result = subprocess.run(  # noqa: S603
                [str(self.bin_dir / args[0]), *args[1:]],
                env=ENV.copy(),
                capture_output=True,
                text=True,
                timeout=30,
                start_new_session=True,
            )
            code = result.returncode
        except (OSError, subprocess.TimeoutExpired):
            raise PostgresError(action) from None
        finally:
            log.info(
                f"local.pg.{action}",
                duration_ms=round((time.monotonic() - started) * 1000),
                exit_code=code,
            )
        if check and code:
            raise PostgresError(action)
        return result

    def ensure_cluster(self) -> None:
        output = self._run("version", ["postgres", "--version"]).stdout
        match = re.search(r"PostgreSQL\) (\d+)", output)
        if match is None:
            raise PostgresVersionError("unknown bundled PostgreSQL major")
        version = self.data_dir / "PG_VERSION"
        if version.exists() and version.read_text().strip().split(".")[0] != match[1]:
            raise PostgresVersionError("cluster major differs from bundle")
        for directory in (self.data_dir, self.run_dir, self.log_file.parent):
            _private_dir(directory)
        if not version.exists():
            self._run(
                "initdb",
                [
                    "initdb",
                    "-D",
                    str(self.data_dir),
                    "-U",
                    "postgres",
                    "-A",
                    "trust",
                    "-E",
                    "UTF8",
                    "--locale=en_US.UTF-8",
                    "--no-instructions",
                ],
            )

    def is_running(self) -> bool:
        return (self.data_dir / "PG_VERSION").exists() and self._run(
            "status", ["pg_ctl", "-D", str(self.data_dir), "status"], check=False
        ).returncode == 0

    def _remember_group(self) -> None:
        pid = int((self.data_dir / "postmaster.pid").read_text().splitlines()[0])
        group = os.getpgid(pid)
        if group == pid and group != os.getpgrp():
            self._group = group
        else:
            raise PostgresError("postmaster has no private process group")

    def _kill_group(self) -> None:
        pid_file = self.data_dir / "postmaster.pid"
        if self._group is None and pid_file.exists():
            lines = pid_file.read_text().splitlines()
            if len(lines) > 1 and Path(lines[1]) == self.data_dir:
                group = int(lines[0])
                # A stale pid file may name a recycled pid: only kill a process that is a postgres.
                if group > 1 and group != os.getpgrp() and self._is_postgres(group):
                    self._group = group
        if self._group is not None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self._group, signal.SIGKILL)
            self._group = None

    def _is_postgres(self, pid: int) -> bool:
        try:
            out = subprocess.run(  # noqa: S603
                ["/bin/ps", "-o", "comm=", "-p", str(pid)],
                env=ENV.copy(),
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            ).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return False
        return Path(out).name == "postgres"

    def start(self) -> None:
        self.ensure_cluster()
        if self.is_running():
            self._remember_group()
            return
        # Children retain the postmaster's group after SIGKILL. Never leave them holding WAL.
        self._kill_group()
        (self.data_dir / "pg_hba.conf").write_text("local all postgres trust\n")
        if self.log_file.exists() and self.log_file.stat().st_size >= 5 * 1024 * 1024:
            self.log_file.replace(self.log_file.with_suffix(".log.1"))
        socket_dir = '"' + str(self.run_dir).replace('"', '""') + '"'
        settings = {
            "listen_addresses": "",
            "unix_socket_directories": socket_dir,
            "unix_socket_permissions": "0700",
            "shared_buffers": "32MB",
            "max_connections": "30",
            "timezone": "UTC",
            "fsync": "on",
            "logging_collector": "off",
            "log_statement": "none",
            "log_connections": "off",
            "log_disconnections": "off",
            "log_min_error_statement": "panic",
            "log_error_verbosity": "terse",
            # An ERROR message can quote a literal value (client text): log only FATAL and PANIC.
            "log_min_messages": "fatal",
        }
        options = shlex.join(
            [part for key, value in settings.items() for part in ("-c", f"{key}={value}")]
        )
        try:
            self._run(
                "start",
                [
                    "pg_ctl",
                    "-D",
                    str(self.data_dir),
                    "-l",
                    str(self.log_file),
                    "-o",
                    options,
                    "-w",
                    "-t",
                    "15",
                    "start",
                ],
            )
            self._remember_group()
            self.wait_ready(15)
            with psycopg.connect(
                make_conninfo(self.conninfo, dbname="postgres"), autocommit=True, connect_timeout=2
            ) as conn:
                ensure_database(conn)
        except Exception:
            self.stop()
            raise

    def wait_ready(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with psycopg.connect(
                    make_conninfo(self.conninfo, dbname="postgres"), connect_timeout=1
                ):
                    return
            except psycopg.OperationalError:
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        raise PostgresError("readiness timeout")

    def stop(self) -> None:
        try:
            if self.is_running():
                self._remember_group()
                self._run(
                    "stop",
                    ["pg_ctl", "-D", str(self.data_dir), "-m", "fast", "-w", "-t", "15", "stop"],
                )
        finally:
            self._kill_group()
            for name in (".s.PGSQL.5432", ".s.PGSQL.5432.lock"):
                (self.run_dir / name).unlink(missing_ok=True)

    def ensure_ready(self) -> str:
        self.ensure_cluster()
        self.start()
        self.wait_ready(15)
        migrate(self.conninfo)
        return self.conninfo
