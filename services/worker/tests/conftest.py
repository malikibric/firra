import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from firra.settings import Settings, get_settings

LOCAL_SECRET = "test-secret-with-at-least-32-characters!!"
LOCAL_KEK_HEX = "00" * 31 + "01"
LOCAL_DB_URL = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"


def local_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "env": "test",
        "database_url": os.environ.get("FIRRA_DATABASE_URL", LOCAL_DB_URL),
        "task_auth_mode": "local_secret",
        "local_task_secret": LOCAL_SECRET,
        "kms_mode": "local",
        "local_kek_hex": LOCAL_KEK_HEX,
        "local_raw_dir": str(Path(tempfile.gettempdir()) / "firra-test-raw"),
    }
    values.update(overrides)
    if values["env"] not in ("local", "test") and "kms_mode" not in overrides:
        values.update(
            kms_mode="cloud", kms_key_name="projects/p/locations/l/keyRings/r/cryptoKeys/k"
        )
        values.pop("local_kek_hex")
        values.pop("local_raw_dir")
        values.setdefault("raw_bucket", "firra-staging-raw")
    if values.get("task_auth_mode") == "oidc":
        values.setdefault("gcp_project_id", "firra-staging")
    return Settings.model_validate(values)


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for key in list(os.environ):
        if key.startswith("FIRRA_") and key != "FIRRA_DATABASE_URL":
            monkeypatch.delenv(key)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _no_gmail_pacing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Fakes answer instantly; pacing itself is covered by test_google_limits.
    monkeypatch.setattr("firra.connectors.google.limits.GMAIL_REQUEST_INTERVAL", 0.0)
