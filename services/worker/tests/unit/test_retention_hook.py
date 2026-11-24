"""The sweep's retention hook reads the owner's period and purges before the right cutoff."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from firra.local.settings import OwnerSettings, save
from firra.maintenance import sweep

NOW = datetime(2026, 10, 5, 12, tzinfo=UTC)


def run(home: Path | None, monkeypatch: pytest.MonkeyPatch) -> list[datetime]:
    cutoffs: list[datetime] = []

    def purge(pool: Any, cutoff: datetime) -> int:
        cutoffs.append(cutoff)
        return 0

    monkeypatch.setattr(sweep, "purge_text_before", purge)
    context: Any = SimpleNamespace(home=home, pool=object())
    sweep.retention(context, NOW)
    return cutoffs


def test_default_is_twelve_months(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert run(tmp_path, monkeypatch) == [NOW - timedelta(days=365)]


def test_custom_period_and_keep_forever_and_no_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save(OwnerSettings(body_retention_months=3), tmp_path)
    assert run(tmp_path, monkeypatch) == [NOW - timedelta(days=91)]
    save(OwnerSettings(body_retention_months=None), tmp_path)
    assert run(tmp_path, monkeypatch) == []
    assert run(None, monkeypatch) == []
