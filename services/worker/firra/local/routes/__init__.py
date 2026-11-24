"""Local ``/v1`` routes: the read models and writes ported from ``apps/api`` (plan F4, L4-02).

Every module exposes ``build_router(ctx: ReadContext) -> APIRouter``; ``all_routers`` lists them
for ``create_local_app``. Response and request shapes are the generated Pydantic models of
``firra_contracts.generated_v1`` (generated from the zod contracts, the source of truth).
"""

from collections.abc import Callable

from fastapi import APIRouter

from firra.ai.local import runtime_for
from firra.local.readctx import ReadContext
from firra.local.routes import (
    ai,
    clients,
    decisions,
    evidence,
    maintenance,
    mcp_connect,
    me,
    onboarding,
    routines,
    setup,
)
from firra.local.settings import load
from firra.local.wipe import Wipe
from firra_contracts import TaskEnvelope


def all_routers(
    ctx: ReadContext,
    *,
    connection_router: APIRouter | None = None,
    processing_enqueue: Callable[[TaskEnvelope], None] | None = None,
    slack_router: APIRouter | None = None,
    relay_router: APIRouter | None = None,
    wipe: Wipe | None = None,
) -> list[APIRouter]:
    from firra.local.mcp.server import build_server, engine_handler
    from firra.local.mcp.transport import http_router

    def mail_scope_changed() -> None:
        if processing_enqueue is not None:
            onboarding.read_mail_again(ctx, processing_enqueue)

    routers = [
        me.build_router(ctx),
        routines.build_router(ctx),
        decisions.build_router(ctx),
        evidence.build_router(ctx),
        clients.build_router(ctx),
        setup.build_router(ctx, mail_scope_changed=mail_scope_changed),
        maintenance.build_router(ctx, wipe=wipe),
        mcp_connect.build_router(ctx),
        http_router(
            build_server(
                engine_handler(ctx, enqueue=processing_enqueue),
                processing_tools=load(ctx.home).mcp_processing_tools,
            )
        ),
    ]

    if processing_enqueue is not None:
        routers.append(onboarding.build_router(ctx, enqueue=processing_enqueue))
    runtime = runtime_for(ctx.pool)
    if runtime is not None:
        routers.append(ai.build_router(runtime))
    if connection_router is not None:
        routers.append(connection_router)
    if slack_router is not None:
        routers.append(slack_router)
    if relay_router is not None:
        routers.append(relay_router)
    return routers
