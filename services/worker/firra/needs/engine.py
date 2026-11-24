"""Stage all paid embeddings before writing any requests or needs."""

from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

from firra.db.jobs import ClaimedJob
from firra.db.needs import NeedDraft, NeedsRepo, PendingRequest
from firra.db.types import WorkspaceId
from firra.llm.service import Llm
from firra.logging import get_logger
from firra.needs.cluster import (
    Request,
    best_service,
    cluster_requests,
    normalize,
    qualifying_window,
)
from firra.needs.config import NeedsConfig

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ClientPlan:
    client_id: UUID
    embedded: list[tuple[UUID, str, tuple[float, ...]]]
    drafts: list[NeedDraft]
    cluster_count: int


def build_plan(
    repo: NeedsRepo,
    llm: Llm,
    *,
    workspace_id: WorkspaceId,
    job: ClaimedJob,
    ref: datetime,
    config: NeedsConfig,
) -> list[ClientPlan]:
    plans: list[ClientPlan] = []
    centroids: dict[tuple[UUID, str], tuple[float, ...]] = {}
    for client_id in repo.clients(workspace_id=workspace_id):
        rows = repo.requests(workspace_id=workspace_id, client_id=client_id, as_of=ref)
        embedded: list[tuple[UUID, str, tuple[float, ...]]] = []
        requests: list[Request] = []
        for item, dedupe_key, occurred_at, thread_key in rows:
            if isinstance(item, PendingRequest):
                vector = tuple(
                    llm.embed(
                        workspace_id=workspace_id,
                        job_id=job.id,
                        text=item.text,
                        task_type="CLUSTERING",
                        dimensionality=config.embedding_dimensions,
                    )
                )
                normalize(vector)
                if len(vector) != config.embedding_dimensions:
                    raise ValueError("request embedding dimension mismatch")
                embedded.append((item.id, item.text, vector))
                request = Request(item.id, dedupe_key, item.text, occurred_at, thread_key, vector)
            else:
                request = item
            requests.append(request)
        clusters = cluster_requests(requests, config.similarity)
        drafts: list[NeedDraft] = []
        for cluster in clusters:
            window = qualifying_window(
                cluster,
                days=config.window_days,
                min_mentions=config.min_mentions,
                min_threads=config.min_threads,
            )
            if window is None:
                continue
            centroids[(client_id, cluster.members[0].dedupe_key)] = cluster.centroid
            drafts.append(
                NeedDraft(
                    cluster_key=cluster.members[0].dedupe_key,
                    request_ids=tuple(request.id for request in window),
                    service_id=None,
                    first_seen_at=window[0].occurred_at,
                    last_seen_at=window[-1].occurred_at,
                )
            )
        plans.append(ClientPlan(client_id, embedded, drafts, len(clusters)))
    if not centroids:
        return plans
    services: list[tuple[UUID, tuple[float, ...]]] = []
    for service_id, text in repo.services(workspace_id=workspace_id):
        vector = tuple(
            llm.embed(
                workspace_id=workspace_id,
                job_id=job.id,
                text=text,
                task_type="CLUSTERING",
                dimensionality=config.embedding_dimensions,
            )
        )
        if len(vector) != config.embedding_dimensions:
            raise ValueError("service embedding dimension mismatch")
        services.append((service_id, normalize(vector)))
    plans = [
        replace(
            plan,
            drafts=[
                replace(
                    draft,
                    service_id=best_service(
                        centroids[(plan.client_id, draft.cluster_key)],
                        services,
                        config.service_similarity,
                    ),
                )
                for draft in plan.drafts
            ],
        )
        for plan in plans
    ]
    return plans


def run_needs(
    repo: NeedsRepo,
    llm: Llm,
    *,
    job: ClaimedJob,
    ref: datetime,
    config: NeedsConfig,
) -> dict[str, int]:
    workspace_id = WorkspaceId(job.workspace_id)
    plans = build_plan(repo, llm, workspace_id=workspace_id, job=job, ref=ref, config=config)
    written = 0
    for plan in plans:
        client_written = repo.save_client(
            workspace_id=workspace_id,
            client_id=plan.client_id,
            embedded=plan.embedded,
            drafts=plan.drafts,
            model=llm.models.model_for("embed"),
            version=config.version,
            min_mentions=config.min_mentions,
            min_threads=config.min_threads,
        )
        written += client_written
        log.info(
            "needs.client_updated",
            client_id=str(plan.client_id),
            requests_embedded=len(plan.embedded),
            clusters=plan.cluster_count,
            needs_written=client_written,
        )
    return {
        "clients": len(plans),
        "requests_embedded": sum(len(plan.embedded) for plan in plans),
        "clusters": sum(plan.cluster_count for plan in plans),
        "needs_written": written,
    }
