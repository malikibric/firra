"""Refresh grants are synthetic and never leave MockTransport."""

from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest

from firra.connectors.errors import TokenRevokedError
from firra.connectors.google.local_tokens import LocalGoogleTokens, token_slot
from firra.db.connections import ConnectionRow
from firra.db.types import WorkspaceId
from firra.local.secrets import SecretStore
from firra.tasks.errors import PermanentTaskError
from tests.fake_google import FakeGoogle


def connection() -> ConnectionRow:
    return ConnectionRow(
        uuid4(),
        WorkspaceId(uuid4()),
        "gmail",
        "user",
        "active",
        "synthetic@example.test",
        "synthetic@example.test",
        None,
        {},
        3,
    )


def resolver(
    fake: FakeGoogle, row: ConnectionRow, *, refresh: str = "before"
) -> tuple[LocalGoogleTokens, SecretStore, list[tuple[str, str]], list[ConnectionRow], list[float]]:
    secrets = SecretStore()
    secrets.set(token_slot(row), refresh)
    sent: list[tuple[str, str]] = []
    revoked: list[ConnectionRow] = []
    now = [0.0]
    auth = LocalGoogleTokens(
        fake.client(),
        secrets,
        client_id="desktop",
        client_secret="synthetic-secret",
        send_secret=lambda n, v: sent.append((n, v)),
        on_revoked=revoked.append,
        clock=lambda: now[0],
    )
    return auth, secrets, sent, revoked, now


def test_cache_refresh_margin_and_shared_calendar_slot() -> None:
    fake, row = FakeGoogle(), connection()
    auth, _, _, _, now = resolver(fake, row)
    calendar = replace(
        row,
        id=uuid4(),
        provider="google_calendar",
        cursor={"google_token_connection_id": str(row.id)},
    )
    assert auth.access_token(row, []) == "access-ok"
    now[0] = 3539
    assert auth.access_token(calendar, []) == "access-ok"
    assert len(fake.calls) == 1
    now[0] = 3540
    assert auth.access_token(calendar, []) == "access-ok"
    assert len(fake.calls) == 2


def test_rotation_updates_memory_and_keychain_sender() -> None:
    fake, row = FakeGoogle(refresh_rotations={"before": "after"}), connection()
    auth, secrets, sent, _, _ = resolver(fake, row)
    assert auth.access_token(row, []) == "access-ok"
    assert secrets.get(token_slot(row)) == "after"
    assert sent == [(token_slot(row), "after")]
    assert auth.access_token(row, []) == "access-ok"
    assert len(fake.calls) == 1
    assert "before" not in repr(auth) and "after" not in repr(auth)


@pytest.mark.parametrize("delete", [False, True])
def test_revocation_signals_only_connection_and_safe_error(delete: bool) -> None:
    fake, row = FakeGoogle(revoked_refresh_tokens={"before"}), connection()
    auth, secrets, _, revoked, _ = resolver(fake, row)
    if delete:
        secrets.delete(token_slot(row))
    with pytest.raises(TokenRevokedError, match=r"^token_revoked$"):
        auth.access_token(row, [])
    assert revoked == [row]


def test_new_consent_and_delete_invalidate_cached_access() -> None:
    fake, row = FakeGoogle(), connection()
    auth, secrets, _, revoked, _ = resolver(fake, row)
    auth.access_token(row, [])
    secrets.set(token_slot(row), "new-consent")
    auth.access_token(row, [])
    assert len(fake.calls) == 2
    secrets.delete(token_slot(row))
    with pytest.raises(TokenRevokedError):
        auth.access_token(row, [])
    assert revoked == [row]


def test_malformed_response_cannot_echo_tokens() -> None:
    row = connection()
    secrets = SecretStore()
    secrets.set(token_slot(row), "sensitive-refresh")
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, json={"access_token": "sensitive-access", "expires_in": "sensitive-expiry"}
            )
        )
    )
    auth = LocalGoogleTokens(
        client,
        secrets,
        client_id="desktop",
        client_secret=None,
        send_secret=lambda *_: None,
        on_revoked=lambda _: None,
    )
    with pytest.raises(PermanentTaskError, match=r"^invalid_token_response$"):
        auth.access_token(row, [])


def test_disconnect_during_refresh_does_not_recreate_slot() -> None:
    row = connection()
    secrets = SecretStore()
    secrets.set(token_slot(row), "before")

    def exchange(_: httpx.Request) -> httpx.Response:
        secrets.delete(token_slot(row))
        return httpx.Response(
            200, json={"access_token": "access", "expires_in": 3600, "refresh_token": "after"}
        )

    sent: list[str] = []
    auth = LocalGoogleTokens(
        httpx.Client(transport=httpx.MockTransport(exchange)),
        secrets,
        client_id="desktop",
        client_secret=None,
        send_secret=lambda n, _: sent.append(n),
        on_revoked=lambda _: None,
    )
    with pytest.raises(TokenRevokedError, match="token_changed"):
        auth.access_token(row, [])
    assert secrets.get(token_slot(row)) is None and not sent


@pytest.mark.parametrize("minutes", [5, 7, 15, 120])
def test_local_sweep_interval_is_scoped_and_idempotent(
    minutes: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime, timedelta
    from typing import cast
    from unittest.mock import MagicMock

    from firra.db.sweeps import SweepTarget
    from firra.maintenance.sweep import run_sweep
    from firra.tasks.context import TaskContext
    from tests.unit.local.test_scheduler import dependencies

    row = connection()
    auth, _, _, _, _ = resolver(FakeGoogle(), row)
    auth.workspace_id = row.workspace_id
    auth.sync_interval_minutes = minutes
    ctx = MagicMock()
    ctx.google = auth
    seen: set[str] = set()

    def create(**values: object) -> UUID | None:
        key = str(values["idempotency_key"])
        if key in seen:
            return None
        seen.add(key)
        return uuid4()

    ctx.jobs.create.side_effect = create
    reader = MagicMock(return_value=[SweepTarget(row.workspace_id, row.id)])
    monkeypatch.setattr("firra.maintenance.sweep.local_active_connections", reader)
    now = datetime(2026, 10, 5, tzinfo=UTC)
    steps = dependencies(
        active_connections=lambda _: pytest.fail("cloud token query in local sweep")
    )
    assert run_sweep(cast(TaskContext, ctx), now, dependencies=steps)["enqueued"] == 1
    assert run_sweep(cast(TaskContext, ctx), now, dependencies=steps)["enqueued"] == 0
    assert (
        run_sweep(cast(TaskContext, ctx), now + timedelta(minutes=minutes), dependencies=steps)[
            "enqueued"
        ]
        == 1
    )
    assert all(":local:" in key for key in seen)
    assert all(call.kwargs["workspace_id"] == row.workspace_id for call in reader.call_args_list)


def test_cloud_sweep_slot_stays_fifteen_minutes() -> None:
    from datetime import UTC, datetime
    from typing import cast
    from unittest.mock import MagicMock

    from firra.db.sweeps import SweepTarget
    from firra.maintenance.sweep import run_sweep
    from firra.tasks.context import TaskContext
    from tests.unit.local.test_scheduler import dependencies

    row = connection()
    ctx = MagicMock()
    ctx.jobs.create.return_value = uuid4()
    run_sweep(
        cast(TaskContext, ctx),
        datetime(2026, 10, 5, 9, 14, tzinfo=UTC),
        dependencies=dependencies(
            active_connections=lambda _: [SweepTarget(row.workspace_id, row.id)]
        ),
    )
    assert ctx.jobs.create.call_args.kwargs["idempotency_key"] == f"sweep:{row.id}:20261005T09q0"


def test_client_comes_from_the_shared_desktop_file_added_later(tmp_path: Path) -> None:
    fake, row = FakeGoogle(), connection()
    secrets = SecretStore()
    secrets.set(token_slot(row), "before")
    config = tmp_path / "google-oauth-client.json"
    auth = LocalGoogleTokens(
        fake.client(),
        secrets,
        client_id=None,
        client_secret=None,
        send_secret=lambda *_: None,
        on_revoked=lambda _: None,
        config_file=config,
    )
    with pytest.raises(PermanentTaskError, match="google_not_configured"):
        auth.access_token(row, [])
    config.write_text('{"installed": {"client_id": "desktop", "client_secret": "s"}}')
    assert auth.access_token(row, [])  # read on use: no engine restart needed


def test_unusable_desktop_file_stays_not_configured(tmp_path: Path) -> None:
    fake, row = FakeGoogle(), connection()
    config = tmp_path / "google-oauth-client.json"
    for content in ("not json", '{"web": {}}', '{"installed": {"client_id": ""}}'):
        config.write_text(content)
        auth = LocalGoogleTokens(
            fake.client(),
            SecretStore(),
            client_id=None,
            client_secret=None,
            send_secret=lambda *_: None,
            on_revoked=lambda _: None,
            config_file=config,
        )
        with pytest.raises(PermanentTaskError, match="google_not_configured"):
            auth.access_token(row, [])


def test_clock_is_wall_time_and_a_401_drops_cached_tokens() -> None:
    import inspect
    import time

    signature = inspect.signature(LocalGoogleTokens.__init__)
    assert signature.parameters["clock"].default is time.time  # monotonic freezes in macOS sleep
    fake, row = FakeGoogle(), connection()
    auth, _, _, _, _ = resolver(fake, row)
    first = auth.access_token(row, [])
    refreshes = len(fake.calls)
    assert auth.access_token(row, []) == first and len(fake.calls) == refreshes  # served from cache
    auth.forget_access_tokens()
    auth.access_token(row, [])
    assert len(fake.calls) == refreshes + 1  # refreshed again after the 401
