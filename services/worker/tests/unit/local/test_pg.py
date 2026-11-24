"""Supervisor commands and filesystem refusal without PostgreSQL binaries."""

import os
import shlex
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from firra.local.pg import (
    ENV,
    PostgresError,
    PostgresSupervisor,
    PostgresVersionError,
    _private_dir,
)


def test_cluster_commands_environment_and_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PGPASSWORD", "private")
    monkeypatch.setenv("HOME", "/private-secret-home")
    pg = PostgresSupervisor(tmp_path / "bin", tmp_path / "data", tmp_path / "run")
    with patch("firra.local.pg.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess([], 0, "postgres (PostgreSQL) 17.11\n", "")
        pg.ensure_cluster()
        assert run.call_args_list[1].args[0] == [
            str(pg.bin_dir / "initdb"),
            "-D",
            str(pg.data_dir),
            "-U",
            "postgres",
            "-A",
            "trust",
            "-E",
            "UTF8",
            "--locale=en_US.UTF-8",
            "--no-instructions",
        ]
        for call in run.call_args_list:
            assert call.kwargs["env"] == ENV
            assert call.kwargs["timeout"] == 30
            assert call.kwargs["start_new_session"] is True
        (pg.data_dir / "PG_VERSION").write_text("16\n")
        run.reset_mock()
        with pytest.raises(PostgresVersionError):
            pg.ensure_cluster()
        assert run.call_count == 1
        assert (pg.data_dir / "PG_VERSION").read_text() == "16\n"


def test_start_settings_hba_and_rotation(tmp_path: Path) -> None:
    pg = PostgresSupervisor(tmp_path / "bin", tmp_path / "data", tmp_path / "run")
    _private_dir(pg.data_dir)
    _private_dir(pg.log_file.parent)
    pg.log_file.write_bytes(b"x" * (5 * 1024 * 1024))
    with (
        patch.object(pg, "ensure_cluster"),
        patch.object(pg, "is_running", return_value=False),
        patch.object(pg, "_run") as run,
        patch.object(pg, "_remember_group"),
        patch.object(pg, "wait_ready"),
        patch("firra.local.pg.psycopg.connect") as connect,
    ):
        connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = (
            1,
        )
        pg.start()
        args = run.call_args.args[1]
        options = shlex.split(args[args.index("-o") + 1])
        assert options == [
            "-c",
            "listen_addresses=",
            "-c",
            f'unix_socket_directories="{pg.run_dir}"',
            "-c",
            "unix_socket_permissions=0700",
            "-c",
            "shared_buffers=32MB",
            "-c",
            "max_connections=30",
            "-c",
            "timezone=UTC",
            "-c",
            "fsync=on",
            "-c",
            "logging_collector=off",
            "-c",
            "log_statement=none",
            "-c",
            "log_connections=off",
            "-c",
            "log_disconnections=off",
            "-c",
            "log_min_error_statement=panic",
            "-c",
            "log_error_verbosity=terse",
            "-c",
            "log_min_messages=fatal",
        ]
    assert (pg.data_dir / "pg_hba.conf").read_text() == "local all postgres trust\n"
    assert pg.log_file.with_suffix(".log.1").stat().st_size == 5 * 1024 * 1024


def test_fallback_and_refusal(tmp_path: Path) -> None:
    pg = PostgresSupervisor(tmp_path, tmp_path / "data", tmp_path / ("long" * 30))
    assert pg.run_dir == Path(f"/tmp/firra-{os.getuid()}")  # noqa: S108
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(PostgresError, match="symlink"):
        _private_dir(link)
    with (
        patch("firra.local.pg.os.getuid", return_value=os.getuid() + 1),
        pytest.raises(PostgresError, match="owned"),
    ):
        _private_dir(tmp_path)


def test_stop_failure_kills_group(tmp_path: Path) -> None:
    pg = PostgresSupervisor(tmp_path, tmp_path / "data", tmp_path / "run")
    pg._group = 12345
    with (
        patch.object(pg, "is_running", return_value=True),
        patch.object(pg, "_remember_group"),
        patch.object(pg, "_run", side_effect=PostgresError("stop")),
        patch("firra.local.pg.os.killpg") as kill,
    ):
        with pytest.raises(PostgresError):
            pg.stop()
        kill.assert_called_once()
