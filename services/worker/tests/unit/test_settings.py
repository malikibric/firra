import pytest
from pydantic import ValidationError

from firra.settings import get_settings
from tests.conftest import LOCAL_KEK_HEX, LOCAL_SECRET, local_settings


def test_local_secret_mode_is_refused_outside_local_and_test() -> None:
    with pytest.raises(ValidationError, match="only allowed in local/test"):
        local_settings(env="staging")


def test_local_secret_must_be_long() -> None:
    with pytest.raises(ValidationError, match=">= 32 chars"):
        local_settings(local_task_secret="short")


def test_oidc_mode_requires_audience_and_invoker() -> None:
    with pytest.raises(ValidationError, match="task_oidc_audience"):
        local_settings(env="staging", task_auth_mode="oidc")


def test_valid_oidc_settings() -> None:
    settings = local_settings(
        env="prod",
        task_auth_mode="oidc",
        task_oidc_audience="https://worker.example.run.app",
        task_invoker_email="tasks@proj.iam.gserviceaccount.com",
        local_task_secret=None,
    )
    assert settings.task_auth_mode == "oidc"


def test_secrets_are_not_rendered() -> None:
    assert LOCAL_SECRET not in repr(local_settings())


def test_empty_env_values_are_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRRA_ENV", "test")
    monkeypatch.setenv("FIRRA_DATABASE_URL", "postgresql://localhost/x")
    monkeypatch.setenv("FIRRA_TASK_AUTH_MODE", "local_secret")
    monkeypatch.setenv("FIRRA_LOCAL_TASK_SECRET", LOCAL_SECRET)
    monkeypatch.setenv("FIRRA_KMS_MODE", "local")
    monkeypatch.setenv("FIRRA_LOCAL_KEK_HEX", LOCAL_KEK_HEX)
    monkeypatch.setenv("FIRRA_LOCAL_RAW_DIR", "local-raw")
    monkeypatch.setenv("FIRRA_SENTRY_DSN", "")
    assert get_settings().sentry_dsn is None


def test_local_kms_mode_is_refused_outside_local_and_test() -> None:
    with pytest.raises(ValidationError, match="local kms mode"):
        local_settings(
            env="staging",
            task_auth_mode="oidc",
            task_oidc_audience="https://w",
            task_invoker_email="i@p.iam.gserviceaccount.com",
            local_task_secret=None,
            kms_mode="local",
        )


def test_cloud_kms_mode_needs_key_name() -> None:
    with pytest.raises(ValidationError, match="kms_key_name"):
        local_settings(kms_mode="cloud", local_kek_hex=None)


def test_push_targets_are_validated() -> None:
    settings = local_settings(
        gmail_push_topic="projects/firra-staging/topics/gmail-push",
        microsoft_notification_url="https://api.example/webhooks/microsoft",
    )
    assert settings.gmail_push_topic == "projects/firra-staging/topics/gmail-push"
    with pytest.raises(ValidationError):
        local_settings(gmail_push_topic="gmail-push")
    with pytest.raises(ValidationError):
        local_settings(microsoft_notification_url="http://api.example/webhooks/microsoft")
    with pytest.raises(ValidationError):
        local_settings(microsoft_notification_url="https://api.example/hook?w=1")


def test_vertex_location_must_be_in_the_eu() -> None:
    assert local_settings(vertex_location="eu", gcp_project_id="p").vertex_location == "eu"
    assert local_settings(vertex_location="europe-west3", gcp_project_id="p").vertex_location
    for location in ("us-central1", "global", "us"):
        with pytest.raises(ValidationError, match="vertex_location"):
            local_settings(vertex_location=location, gcp_project_id="p")


def test_vertex_needs_a_project() -> None:
    with pytest.raises(ValidationError, match="gcp_project_id"):
        local_settings(vertex_location="eu")


def test_embed_location_must_be_an_eu_location_and_needs_the_generation_location() -> None:
    ok = local_settings(
        vertex_location="eu", vertex_embed_location="europe-west3", gcp_project_id="p"
    )
    assert ok.vertex_embed_location == "europe-west3"
    with pytest.raises(ValidationError, match="vertex_embed_location"):
        local_settings(
            vertex_location="eu", vertex_embed_location="us-central1", gcp_project_id="p"
        )
    with pytest.raises(ValidationError, match="vertex_embed_location needs vertex_location"):
        local_settings(vertex_embed_location="europe-west3")
