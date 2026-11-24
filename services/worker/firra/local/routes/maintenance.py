"""Owner maintenance on the local API: diagnostics export and encrypted backup/restore.

The engine never writes to a path the caller names: backups and diagnostics travel in the response
body and the app saves them where the owner chooses (a save panel). Contract: ``/local/*`` of
``docs/plans/local/contracts.md`` section 3.
"""

import base64
import binascii
import platform
from datetime import UTC, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from firra.backup import crypto
from firra.backup.service import RestoreError, export_backup, restore_backup
from firra.db import local_diagnostics
from firra.db.learning import LearningRepo
from firra.db.local_retention import purge_text_before
from firra.db.types import WorkspaceId
from firra.learning.config import load_config
from firra.learning.metrics import episodes, lead_time, precision, usefulness
from firra.learning.report import report_json
from firra.local.readctx import ReadContext
from firra.local.server import ApiError
from firra.local.settings import load, save
from firra.local.wipe import Wipe, delete_everything
from firra_contracts.generated.local.backup_create_response import BackupCreateResponse
from firra_contracts.generated.local.backup_restore_request import BackupRestoreRequest
from firra_contracts.generated.local.backup_restore_response import BackupRestoreResponse
from firra_contracts.generated.local.diagnostics import Diagnostics
from firra_contracts.generated.local.metrics_export import MetricsExport
from firra_contracts.generated.local.retention_settings import RetentionSettings


class DeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: str


def _dir_bytes(path: Path | None) -> int:
    if path is None or not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file() and not f.is_symlink())


def build_router(ctx: ReadContext, *, wipe: Wipe | None = None) -> APIRouter:
    router = APIRouter(prefix="/local")
    workspace = WorkspaceId(ctx.identity.workspace_id)

    @router.post("/diagnostics/export")
    def diagnostics() -> Any:
        with ctx.pool.connection() as conn:
            counts = local_diagnostics.collect(conn, workspace)
        body = Diagnostics.model_validate(
            {
                "generated_at": ctx.clock(),
                "engine_version": ctx.engine_version,
                "platform": platform.platform(),
                "data_bytes": _dir_bytes(ctx.home),
                **counts,
            }
        )
        return body.model_dump(mode="json")

    @router.post("/metrics/export")
    def metrics_export() -> Any:
        now = ctx.clock()
        as_of = now.date()
        repo = LearningRepo(ctx.pool)
        config = load_config()
        feedback = repo.feedback(workspace_id=workspace, as_of=as_of)
        alerts = episodes(repo.assessments(workspace_id=workspace, as_of=as_of))
        outcomes = repo.outcomes(workspace_id=workspace, as_of=as_of)
        report = report_json(
            workspace,
            as_of,
            usefulness(feedback, config.min_rated),
            precision(alerts, outcomes, as_of, config.horizon_days, config.min_rated),
            lead_time(alerts, outcomes),
        )
        del report["workspace_id"]  # an install id is not client data, but nothing here needs it
        body = MetricsExport.model_validate(
            {
                **report,
                "generated_at": now,
                "engine_version": ctx.engine_version,
                "important_per_week": repo.important_per_week(workspace_id=workspace, as_of=as_of),
            }
        )
        return body.model_dump(mode="json")

    @router.post("/backup/create")
    def backup_create() -> Any:
        phrase = crypto.new_recovery_phrase()
        now = ctx.clock()
        with ctx.pool.connection() as conn:
            blob = export_backup(conn, workspace, phrase, now=now)
        body = BackupCreateResponse.model_validate(
            {
                "recovery_phrase": phrase,
                "filename": f"firra-backup-{now.astimezone(UTC):%Y%m%d}.firrabackup",
                "backup_b64": base64.b64encode(blob).decode(),
            }
        )
        return body.model_dump(mode="json")

    @router.post("/backup/restore")
    def backup_restore(request: BackupRestoreRequest) -> Any:
        try:
            blob = base64.b64decode(request.backup_b64, validate=True)
        except (binascii.Error, ValueError):
            raise ApiError(422, "invalid_backup", "The backup file could not be read.") from None
        try:
            with ctx.pool.connection() as conn:
                result = restore_backup(conn, workspace, blob, request.phrase)
        except crypto.BackupDecryptError:
            raise ApiError(
                422, "invalid_backup", "Wrong recovery phrase, or the file was changed."
            ) from None
        except RestoreError:
            raise ApiError(409, "workspace_not_empty", "Restore needs a fresh install.") from None
        return BackupRestoreResponse.model_validate(
            {
                "clients": result["clients"],
                "pending_channel_mappings": len(result["pending_scopes"]),
                "pending_feedback": len(result["pending_feedback"]),
            }
        ).model_dump(mode="json")

    @router.get("/retention")
    def retention() -> Any:
        return RetentionSettings(body_months=load(ctx.home).body_retention_months).model_dump(
            mode="json"
        )

    @router.put("/retention")
    def update_retention(body: dict[str, Any]) -> Any:
        months = body.get("body_months") if set(body) == {"body_months"} else -1
        valid = months is None or (
            isinstance(months, int) and not isinstance(months, bool) and 1 <= months <= 120
        )
        if not valid:
            raise ApiError(422, "invalid_request", "body_months must be 1 to 120 or null.")
        save(load(ctx.home).model_copy(update={"body_retention_months": months}), ctx.home)
        if months is not None:  # shortening applies at once
            purge_text_before(ctx.pool, ctx.clock() - timedelta(days=round(months * 30.44)))
        return RetentionSettings(body_months=months).model_dump(mode="json")

    @router.post("/data")
    def delete_data(request: DeleteRequest) -> Any:
        """Delete everything (plan L8-03); counts only in the answer."""
        if request.confirm != "delete everything":
            raise ApiError(422, "invalid_request", "Type the confirmation phrase to delete.")
        return delete_everything(ctx.pool, wipe)

    return router
