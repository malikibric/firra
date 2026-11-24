"""Only fixture text and random synthetic keys; failures never include either."""

import base64
import os
from pathlib import Path

import pytest
from tests.conftest import local_settings

from firra.local.bodycrypt import BodyCryptError, BodyKeyProvider, bind, decrypt, encrypt, read_body
from firra.local.profile import build_settings
from firra.local.secrets import MissingSecretError, SecretStore
from firra.settings import Settings


def test_round_trip_and_nonce_uniqueness() -> None:
    key = os.urandom(32)
    tokens = {encrypt(key, "Fixture text Čć 💬", "workspace:record") for _ in range(1000)}
    assert len(tokens) == 1000
    assert all(decrypt(key, token, "workspace:record") == "Fixture text Čć 💬" for token in tokens)
    assert decrypt(key, encrypt(key, "", "aad"), "aad") == ""


def test_wrong_key_aad_tamper_version_and_length() -> None:
    key = os.urandom(32)
    token = encrypt(key, "PRIVATE_BODY", "row")
    sealed = bytearray(base64.b64decode(token.removeprefix("enc:v1:")))
    sealed[-1] ^= 1
    cases = [
        (os.urandom(32), token, "row"),
        (key, token, "other"),
        (key, "enc:v1:" + base64.b64encode(sealed).decode(), "row"),
        (key, token.replace("enc:v1:", "enc:v2:"), "row"),
        (key, "plaintext", "row"),
        (key, "enc:v1:???", "row"),
        (key, "enc:v1:", "row"),
        (b"bad", token, "row"),
    ]
    for secret, value, aad in cases:
        with pytest.raises(BodyCryptError) as error:
            decrypt(secret, value, aad)
        assert str(error.value) == "body_decrypt_failed"
    with pytest.raises(BodyCryptError):
        encrypt(b"bad", "PRIVATE_BODY", "row")


def test_secret_provider_and_explicit_import_flag() -> None:
    secrets = SecretStore()
    provider = BodyKeyProvider(secrets)
    with pytest.raises(MissingSecretError):
        provider.key()
    for value in ("garbage", base64.b64encode(b"short").decode()):
        secrets.set("db_encryption_key", value)
        with pytest.raises(BodyCryptError) as error:
            provider.key()
        assert value not in str(error.value)
    secret = base64.b64encode(os.urandom(32)).decode()
    secrets.set("db_encryption_key", secret)
    assert len(provider.key()) == 32
    assert secret not in repr(provider)

    class Pool:
        pass

    pool = Pool()
    bind(pool, provider)
    with pytest.raises(BodyCryptError):
        read_body(pool, "ws", "id", "legacy plaintext")
    bind(pool, BodyKeyProvider(secrets, allow_plaintext_migration=True))
    assert read_body(pool, "ws", "id", "legacy plaintext") == "legacy plaintext"
    with pytest.raises(BodyCryptError):
        read_body(pool, "ws", "id", "enc:v2:future")
    secrets.delete("db_encryption_key")
    with pytest.raises(MissingSecretError):
        provider.key()


def test_local_profile_has_no_cloud_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FIRRA_PROFILE", "local")
    monkeypatch.setenv("FIRRA_GCP_PROJECT_ID", "unwanted-cloud")
    settings = build_settings("host=/tmp/synthetic user=postgres dbname=firra", tmp_path)
    assert settings.profile == "local"
    assert settings.task_auth_mode == settings.kms_mode == "none"
    assert all(
        value is None
        for value in (settings.local_task_secret, settings.local_kek_hex, settings.kms_key_name)
    )
    assert settings.gcp_project_id is settings.vertex_location is settings.raw_bucket is None
    assert settings.google_client_id is settings.microsoft_client_id is None
    assert settings.database_url.get_secret_value().startswith("host=/tmp/")
    assert all(
        path.stat().st_mode & 0o777 == 0o700
        for path in [tmp_path, tmp_path / "raw", tmp_path / "log", tmp_path / "run"]
    )
    monkeypatch.delenv("FIRRA_PROFILE")
    assert local_settings().profile == "cloud"  # Existing validation/profile stays untouched.


def test_local_profile_defaults_without_cloud_credentials() -> None:
    settings = Settings.model_validate({"profile": "local", "database_url": "host=/tmp/fixture"})
    assert settings.task_auth_mode == "none" and settings.kms_mode == "none"
    assert settings.local_task_secret is None and settings.local_kek_hex is None
