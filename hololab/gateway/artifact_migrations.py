"""Authenticated HTTP control plane for the local artifact migration worker."""

from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

from hololab.artifact_migration_service import MigrationService
from hololab.persistence.db import Database


def service(request: Request, authorization: str | None = Header(default=None)):
    svc = getattr(request.app.state, "artifact_migrations", None)
    expected = getattr(request.app.state, "artifact_migration_token", None)
    if svc is None or not expected:
        raise HTTPException(503, "Use the authenticated local migration maintenance server")
    supplied = (authorization or "").removeprefix("Bearer ")
    if not secrets.compare_digest(supplied, expected):
        raise HTTPException(401, "Invalid maintenance credential")
    return svc


router = APIRouter(
    prefix="/api/artifact-migrations", tags=["artifact-migrations"], dependencies=[Depends(service)]
)


class Plan(BaseModel):
    node_id: str
    source: str | None = None
    target: str
    handle_id: str | None = None
    workspace: bool = False
    adopt_existing: bool = False
    preserve_existing_missing: bool = False


class Execute(BaseModel):
    plan_id: str
    plan_digest: str
    idempotency_key: str
    repair_existing: bool = False


class Finalize(BaseModel):
    delete_source: bool = False


@router.post("/plan")
async def plan(body: Plan, request: Request):
    return await service_instance(request).plan(**body.model_dump())


def service_instance(request):
    return request.app.state.artifact_migrations


@router.post("", status_code=202)
async def execute(body: Execute, request: Request):
    return await service_instance(request).start(
        body.plan_id, body.plan_digest, body.idempotency_key, body.repair_existing
    )


@router.get("/{mid}")
async def status(mid: str, request: Request):
    return await service_instance(request).status(mid)


@router.post("/{mid}/resume", status_code=202)
async def resume(mid: str, request: Request):
    svc = service_instance(request)
    m = await svc.store.get(mid)
    return await svc.start(mid, m["plan_digest"], m["idempotency_key"] or mid)


@router.post("/{mid}/cancel")
async def cancel(mid: str, request: Request):
    return await service_instance(request).cancel(mid)


@router.post("/{mid}/rollback")
async def rollback(mid: str, request: Request):
    return await service_instance(request).rollback(mid)


@router.post("/{mid}/finalize")
async def finalize(mid: str, body: Finalize, request: Request):
    return await service_instance(request).finalize(mid, body.delete_source)


def maintenance_app(
    *, db_path: Path, node_id: str, roots: list[Path], token: str, gateway_url=None
):
    """No node startup, orphan sweeping, flag resets or GPU activity.

    This sidecar permits installing a new maintenance feature without replacing
    the running gateway. All writes use the official Database writer service.
    Database triggers fence the old gateway as well as this process.
    """

    @asynccontextmanager
    async def lifespan(app):
        db = Database(db_path)
        await db.open()
        app.state.artifact_migrations = MigrationService(
            db, node_id=node_id, allowed_roots=roots, gateway_url=gateway_url
        )
        app.state.artifact_migration_token = token
        try:
            yield
        finally:
            await app.state.artifact_migrations.close()
            await db.close()

    app = FastAPI(title="HoloLab artifact migration maintenance", lifespan=lifespan)
    app.include_router(router)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=409, content={"detail": str(exc)})

    return app
