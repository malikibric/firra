"""FastAPI application factory: `uvicorn --factory firra.app:create_app`."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from psycopg import Connection
from psycopg.rows import DictRow
from psycopg_pool import ConnectionPool

from firra.connectors.google.auth import GoogleAuth
from firra.connectors.google.credentials import GoogleDelegatedTokens, GoogleUserTokens
from firra.connectors.microsoft.auth import MicrosoftAuth
from firra.connectors.microsoft.credentials import MicrosoftAppTokens, MicrosoftUserTokens
from firra.crypto.kek import CloudKmsKek, Kek, LocalKek, MetadataTokenSource
from firra.crypto.keyring import Keyring
from firra.db import pool as db_pool
from firra.db.connections import ConnectionRepo
from firra.db.jobs import PgJobStore
from firra.db.keys import PgWorkspaceKeyStore
from firra.db.llm_usage import LlmUsageRepo
from firra.db.records import RecordRepo
from firra.llm.models import ModelSet
from firra.llm.service import Llm
from firra.llm.tokens import GcloudTokenSource
from firra.llm.vertex import VertexClient
from firra.logging import configure_logging, get_logger
from firra.maintenance.router import router as maintenance_router
from firra.sentry import init_sentry
from firra.settings import Settings, get_settings
from firra.storage.raw import GcsRawStore, LocalRawStore, RawStore
from firra.tasks.auth import TaskAuthenticator, google_jwks
from firra.tasks.context import TaskContext
from firra.tasks.queue import CloudTasksQueue, LocalQueue, TaskQueue, local_delivery_client
from firra.tasks.router import router as tasks_router

log = get_logger(__name__)


def _kek(settings: Settings, http: httpx.Client, tokens: MetadataTokenSource) -> Kek:
    if settings.kms_mode == "local" and settings.local_kek_hex is not None:
        return LocalKek(bytes.fromhex(settings.local_kek_hex.get_secret_value()))
    return CloudKmsKek(name=settings.kms_key_name or "", client=http, tokens=tokens)


def _raw_store(settings: Settings, http: httpx.Client, tokens: MetadataTokenSource) -> RawStore:
    if settings.local_raw_dir:
        return LocalRawStore(Path(settings.local_raw_dir))
    return GcsRawStore(bucket=settings.raw_bucket or "", client=http, tokens=tokens)


def _queue(settings: Settings, http: httpx.Client, tokens: MetadataTokenSource) -> TaskQueue:
    if settings.task_auth_mode == "local_secret" and settings.local_task_secret is not None:
        return LocalQueue(
            settings.local_worker_url,
            settings.local_task_secret.get_secret_value(),
            local_delivery_client(),
        )
    return CloudTasksQueue(
        project_id=settings.gcp_project_id or "",
        region=settings.gcp_region,
        worker_url=settings.task_oidc_audience or "",
        invoker_email=settings.task_invoker_email or "",
        client=http,
        tokens=tokens,
    )


def _llm(
    settings: Settings,
    pool: ConnectionPool[Connection[DictRow]],
    http: httpx.Client,
    tokens: MetadataTokenSource,
) -> Llm | None:
    if not settings.vertex_location or not settings.gcp_project_id:
        return None
    return Llm(
        backend=VertexClient(
            project_id=settings.gcp_project_id,
            location=settings.vertex_location,
            embed_location=settings.vertex_embed_location,
            http=http,
            tokens=GcloudTokenSource() if settings.vertex_token_source == "gcloud" else tokens,  # noqa: S105 - selector
        ),
        models=ModelSet(
            location=settings.vertex_location,
            extract=settings.model_extract,
            synth=settings.model_synth,
            embed=settings.model_embed,
            synthdata=settings.model_synthdata,
        ),
        usage=LlmUsageRepo(pool),
        hard_cap_usd=settings.llm_hard_cap_usd,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    init_sentry(settings)
    pool = db_pool.create_pool(settings)
    http = httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0))
    tokens = MetadataTokenSource(http)
    jobs = PgJobStore(pool)
    keyring = Keyring(kek=_kek(settings, http, tokens), store=PgWorkspaceKeyStore(pool))
    google = GoogleAuth(
        user=GoogleUserTokens(
            settings.google_client_id, settings.google_client_secret.get_secret_value(), http
        )
        if settings.google_client_id and settings.google_client_secret
        else None,
        delegated=GoogleDelegatedTokens(settings.worker_service_account, http, tokens)
        if settings.worker_service_account
        else None,
        keyring=keyring,
    )
    connections = ConnectionRepo(pool)
    microsoft_secret = (
        settings.microsoft_client_secret.get_secret_value()
        if settings.microsoft_client_id and settings.microsoft_client_secret
        else None
    )
    microsoft = MicrosoftAuth(
        user=MicrosoftUserTokens(settings.microsoft_client_id or "", microsoft_secret, http)
        if microsoft_secret
        else None,
        app=MicrosoftAppTokens(settings.microsoft_client_id or "", microsoft_secret, http)
        if microsoft_secret
        else None,
        keyring=keyring,
        connections=connections,
    )
    task_context = TaskContext(
        pool=pool,
        jobs=jobs,
        connections=connections,
        records=RecordRepo(pool),
        raw=_raw_store(settings, http, tokens),
        keyring=keyring,
        queue=_queue(settings, http, tokens),
        http=http,
        google=google,
        microsoft=microsoft,
        time_budget_seconds=settings.task_time_budget_seconds,
        llm=_llm(settings, pool, http, tokens),
        processing_enabled=settings.processing_enabled,
        gmail_push_topic=settings.gmail_push_topic,
        microsoft_notification_url=settings.microsoft_notification_url,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        pool.open()
        log.info("worker.started", env=settings.env, release=settings.release)
        try:
            yield
        finally:
            # Both block until in-flight work finishes; keep the event loop free.
            await asyncio.to_thread(pool.close)
            await asyncio.to_thread(http.close)
            if isinstance(task_context.queue, LocalQueue):
                await asyncio.to_thread(task_context.queue.client.close)

    is_local = settings.env in ("local", "test")
    app = FastAPI(
        title="firra-worker",
        lifespan=lifespan,
        docs_url="/docs" if is_local else None,
        redoc_url=None,
        openapi_url="/openapi.json" if is_local else None,
    )
    app.state.task_authenticator = TaskAuthenticator(settings=settings, jwks=google_jwks())
    app.state.job_store = jobs
    app.state.task_context = task_context
    app.include_router(tasks_router)
    app.include_router(maintenance_router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    def ready(_request: Request) -> JSONResponse:
        try:
            ready = db_pool.ping(pool)
        except Exception:
            log.error("worker.ready_failed", exc=True)
            ready = False
        return JSONResponse({"status": "ok" if ready else "unavailable"}, 200 if ready else 503)

    return app
