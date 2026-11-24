"""Worker configuration, read from FIRRA_* environment variables and validated at startup."""

from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "staging", "prod"]
_MODEL_ID = r"^[a-z0-9][a-z0-9.-]{1,62}$"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FIRRA_", extra="ignore", frozen=True, env_ignore_empty=True
    )

    profile: Literal["cloud", "local"] = "cloud"
    env: Environment
    database_url: SecretStr
    db_pool_min: int = Field(default=1, ge=0)
    db_pool_max: int = Field(default=5, ge=1)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # Cloud Tasks authentication. "local_secret" exists only for the local dispatcher.
    task_auth_mode: Literal["oidc", "local_secret", "none"] = "oidc"
    task_oidc_audience: str | None = None
    task_invoker_email: str | None = None
    local_task_secret: SecretStr | None = None
    # Continuations of long jobs are enqueued by the worker itself.
    gcp_project_id: str | None = None
    gcp_region: str = "europe-west3"
    local_worker_url: str = "http://127.0.0.1:8081"

    # Key-encryption key for connection tokens: Cloud KMS, or a static key for local/test only.
    kms_mode: Literal["cloud", "local", "none"] = "cloud"
    kms_key_name: str | None = None
    local_kek_hex: SecretStr | None = None

    # Raw payload bucket (15-day lifecycle); local/test may use a directory instead.
    raw_bucket: str | None = None
    local_raw_dir: str | None = None

    # Google OAuth client (user connections) and the worker's own service account (delegation).
    sync_interval_minutes: int = Field(default=5, ge=5)
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    worker_service_account: str | None = None
    # Microsoft Entra multi-tenant app: delegated (user) and application-permission connections.
    microsoft_client_id: str | None = None
    microsoft_client_secret: SecretStr | None = None
    # Push notifications: Pub/Sub topic Gmail publishes to (projects/<p>/topics/gmail-push) and the
    # api endpoint Graph posts change notifications to (https://<api>/webhooks/microsoft).
    gmail_push_topic: str | None = Field(
        default=None, pattern=r"^projects/[a-z0-9-]+/topics/[\w.~+%-]+$"
    )
    microsoft_notification_url: str | None = Field(default=None, pattern=r"^https://[^?#]+$")
    # Vertex AI Gemini (the design notes): EU multi-region "eu" or a single europe-* region.
    # Unset = no model calls in this environment (tests fake every call).
    vertex_location: str | None = Field(default=None, pattern=r"^(eu|europe-[a-z]+[0-9]+)$")
    # Separate location for embeddings when the model is not offered on the multi-region.
    vertex_embed_location: str | None = Field(default=None, pattern=r"^(eu|europe-[a-z]+[0-9]+)$")
    vertex_token_source: Literal["metadata", "gcloud"] = "metadata"  # noqa: S105 - selector
    llm_hard_cap_usd: Decimal | None = Field(default=None, gt=0)
    model_extract: str | None = Field(default=None, pattern=_MODEL_ID)
    model_synth: str | None = Field(default=None, pattern=_MODEL_ID)
    model_embed: str | None = Field(default=None, pattern=_MODEL_ID)
    model_synthdata: str | None = Field(default=None, pattern=_MODEL_ID)
    # Extraction spends money on every record; it runs only where explicitly switched on.
    processing_enabled: bool = False
    # Long tasks stop and enqueue a continuation after this many seconds (Cloud Run limit is 600).
    task_time_budget_seconds: float = Field(default=480.0, gt=0, le=540)

    sentry_dsn: SecretStr | None = None
    release: str = "dev"

    @model_validator(mode="before")
    @classmethod
    def _local_defaults(cls, values: Any) -> Any:
        if isinstance(values, dict) and values.get("profile") == "local":
            values = dict(values)
            values.setdefault("env", "local")
            values.setdefault("task_auth_mode", "none")
            values.setdefault("kms_mode", "none")
            values.setdefault(
                "local_raw_dir", str(Path.home() / "Library/Application Support/Firra/raw")
            )
        return values

    @model_validator(mode="after")
    def _check_vertex_token_source(self) -> Self:
        if self.vertex_token_source == "gcloud" and self.env not in ("local", "test"):  # noqa: S105
            raise ValueError("gcloud Vertex tokens are only allowed in local/test")
        return self

    @model_validator(mode="after")
    def _check_task_auth(self) -> Self:
        if self.profile == "local":
            if (
                self.env not in ("local", "test")
                or self.task_auth_mode != "none"
                or self.kms_mode != "none"
                or not self.local_raw_dir
                or self.local_task_secret is not None
                or self.local_kek_hex is not None
                or self.kms_key_name is not None
                or self.raw_bucket is not None
                or self.gcp_project_id is not None
                or self.vertex_location is not None
                or self.vertex_embed_location is not None
            ):
                raise ValueError("local profile requires private paths and in-process services")
            return self
        if self.task_auth_mode == "none" or self.kms_mode == "none":
            raise ValueError("disabled task auth and KMS require the local profile")
        if self.task_auth_mode == "oidc":
            if not self.task_oidc_audience or not self.task_invoker_email:
                raise ValueError("oidc task auth needs task_oidc_audience and task_invoker_email")
            if not self.gcp_project_id:
                raise ValueError("oidc task auth needs gcp_project_id (to enqueue continuations)")
        else:
            if self.env not in ("local", "test"):
                raise ValueError("local_secret task auth is only allowed in local/test")
            secret = self.local_task_secret
            if secret is None or len(secret.get_secret_value()) < 32:
                raise ValueError("local_secret task auth needs local_task_secret (>= 32 chars)")
        if self.kms_mode == "cloud" and not self.kms_key_name:
            raise ValueError("cloud kms mode needs kms_key_name")
        if self.kms_mode == "local":
            if self.env not in ("local", "test"):
                raise ValueError("local kms mode is only allowed in local/test")
            key = self.local_kek_hex.get_secret_value() if self.local_kek_hex else ""
            if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
                raise ValueError("local kms mode needs local_kek_hex (64 hex characters)")
        if self.local_raw_dir and self.env not in ("local", "test"):
            raise ValueError("local_raw_dir is only allowed in local/test")
        if self.vertex_location and not self.gcp_project_id:
            raise ValueError("vertex_location needs gcp_project_id")
        if self.vertex_embed_location and not self.vertex_location:
            raise ValueError("vertex_embed_location needs vertex_location")
        if not self.local_raw_dir and not self.raw_bucket:
            raise ValueError("raw_bucket (or local_raw_dir in local/test) is required")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # values come from the environment
