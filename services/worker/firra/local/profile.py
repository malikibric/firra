"""The local engine profile: private paths, memory-only keys and in-process delivery."""

import shutil
from pathlib import Path
from typing import cast

import httpx
from pydantic import SecretStr

from firra.ai.local import AiRuntime
from firra.connectors.google.local_tokens import LocalGoogleTokens
from firra.connectors.microsoft.auth import MicrosoftAuth
from firra.connectors.slack.local_tokens import LocalSlackTokens
from firra.crypto.kek import LocalKek
from firra.crypto.keyring import Keyring
from firra.db.connections import ConnectionRepo, ConnectionRow
from firra.db.jobs import PgJobStore
from firra.db.keys import PgWorkspaceKeyStore
from firra.db.local_identity import LocalIdentity
from firra.db.local_secure import protect_legacy_fields
from firra.db.pool import create_pool
from firra.db.records import RecordRepo
from firra.db.types import WorkspaceId
from firra.local.bodycrypt import BodyKeyProvider, bind
from firra.local.control import ControlServer
from firra.local.executor import LocalExecutor
from firra.local.scheduler import LocalScheduler
from firra.local.secrets import SecretStore
from firra.settings import Settings
from firra.storage.raw import NoRawStore
from firra.tasks.context import TaskContext
from firra.tasks.queue import TaskQueue


def build_settings(conninfo: str, home: Path | None = None) -> Settings:
    home = home or Path.home() / "Library/Application Support/Firra"
    for path in (home, home / "raw", home / "log", home / "run"):
        if path.is_symlink():
            raise ValueError("local profile directory is a symlink")
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.chmod(0o700)
    # Explicit values prevent inherited cloud environment configuration leaking into this profile.
    return Settings(
        profile="local",
        env="local",
        database_url=SecretStr(conninfo),
        local_raw_dir=str(home / "raw"),
        task_auth_mode="none",
        local_task_secret=None,
        task_oidc_audience=None,
        task_invoker_email=None,
        kms_mode="none",
        kms_key_name=None,
        local_kek_hex=None,
        raw_bucket=None,
        gcp_project_id=None,
        vertex_location=None,
        vertex_embed_location=None,
        worker_service_account=None,
        microsoft_client_id=None,
        microsoft_client_secret=None,
        gmail_push_topic=None,
        microsoft_notification_url=None,
        processing_enabled=False,
        sentry_dsn=None,
    )


class LocalRuntime:
    """Owns the resources the Engine starts after its secrets/PG handshake."""

    def __init__(
        self,
        settings: Settings,
        secrets: SecretStore,
        *,
        identity: LocalIdentity | None = None,
        control: ControlServer | None = None,
    ) -> None:
        provider = BodyKeyProvider(secrets)
        key = provider.key()
        self.pool = create_pool(settings)
        bind(self.pool, provider)
        self.http = httpx.Client(timeout=httpx.Timeout(30, connect=10))
        self.pool.open()
        connections = ConnectionRepo(self.pool, local=True)
        keyring = Keyring(LocalKek(key), PgWorkspaceKeyStore(self.pool))

        def on_revoked(connection: ConnectionRow) -> None:
            connections.mark_failed(
                workspace_id=connection.workspace_id,
                connection_id=connection.id,
                error_class="token_revoked",
                revoked=True,
            )
            if control is not None:
                control.send_event("need_reauth", str(connection.id))

        google = LocalGoogleTokens(
            self.http,
            secrets,
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret.get_secret_value()
            if settings.google_client_secret
            else None,
            send_secret=control.send_secret if control else lambda *_: None,
            on_revoked=on_revoked,
            workspace_id=WorkspaceId(identity.workspace_id) if identity else None,
            sync_interval_minutes=settings.sync_interval_minutes,
            config_file=(
                Path(settings.local_raw_dir).parent / "google-oauth-client.json"
                if settings.local_raw_dir
                else None
            ),
        )
        context = TaskContext(
            pool=self.pool,
            jobs=PgJobStore(self.pool),
            connections=connections,
            records=RecordRepo(self.pool),
            raw=NoRawStore(),
            keyring=keyring,
            queue=cast(TaskQueue, None),
            http=self.http,
            google=google,
            microsoft=MicrosoftAuth(None, None, keyring, connections),
            time_budget_seconds=settings.task_time_budget_seconds,
            slack=LocalSlackTokens(secrets, on_revoked=on_revoked),
            home=Path(settings.local_raw_dir).parent if settings.local_raw_dir else None,
            processing_enabled=True,
        )
        self.ai = (
            AiRuntime(
                self.pool,
                WorkspaceId(identity.workspace_id),
                Path(settings.local_raw_dir or "").parent,
            )
            if identity is not None
            else None
        )
        # The executor replaces the bootstrap queue before any handler can run.
        self.executor = LocalExecutor(context)
        self.context = self.executor.context
        self.scheduler = LocalScheduler(self.context)
        self._legacy_raw_dir = Path(settings.local_raw_dir) if settings.local_raw_dir else None

    def start(self) -> None:
        # Source-layer S0: no plain provider payloads on disk, no plain subjects in the database.
        if self._legacy_raw_dir is not None:
            shutil.rmtree(self._legacy_raw_dir, ignore_errors=True)
        protect_legacy_fields(self.pool)
        self.executor.start()
        self.scheduler.start()

    def stop(self) -> None:
        self.scheduler.stop()
        self.executor.stop()
        self.pool.close()
        self.http.close()
