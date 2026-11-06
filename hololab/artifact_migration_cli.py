"""Thin CLI for the authenticated migration HTTP control plane."""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

import httpx
import typer

artifacts_app = typer.Typer(help="Manage produced artifacts.")
migrate = typer.Typer(help="Plan and execute verified artifact migrations.")
artifacts_app.add_typer(migrate, name="migrate")


@migrate.callback()
def configure(
    ctx: typer.Context,
    url: str = typer.Option("http://127.0.0.1:8830", "--url"),
    token_file: Path = typer.Option(Path("~/.hololab/migration.token"), "--token-file"),
):
    ctx.obj = {"url": url.rstrip("/"), "token_file": token_file.expanduser()}


def request(ctx, method, suffix="", body=None):
    token = ctx.obj["token_file"].read_text().strip()
    # Full verification of hundreds of GB can take much longer than two
    # minutes. A client timeout does not cancel the server-side filesystem work.
    timeout = None if suffix.endswith(("/finalize", "/rollback")) else 120
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        r = client.request(
            method,
            ctx.obj["url"] + "/api/artifact-migrations" + suffix,
            headers={"Authorization": "Bearer " + token},
            json=body,
        )
    if r.is_error:
        raise typer.BadParameter(f"{r.status_code}: {r.text}")
    return r.json()


def show(data):
    result = {
        k: data.get(k)
        for k in (
            "migration_id",
            "state",
            "plan_digest",
            "epoch",
            "error",
            "worker_running",
            "filesystem_locked",
            "local_progress",
        )
        if k in data
    }
    result["units"] = len(data.get("units", []))
    result["handles"] = len(data.get("plan", {}).get("handles", []))
    result["source"] = data.get("source")
    result["target"] = data.get("target")
    result["aliases"] = data.get("aliases", [])
    result["preflight"] = {
        k: v for k, v in data.get("plan", {}).get("preflight", {}).items() if k != "missing"
    }
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@migrate.command()
def plan(
    ctx: typer.Context,
    node: str = typer.Option(..., "--node"),
    target_root: str = typer.Option(..., "--target-root"),
    path: str | None = typer.Option(None, "--path"),
    handle: str | None = typer.Option(None, "--handle"),
    workspace: bool = typer.Option(False, "--workspace"),
    source_root: str | None = typer.Option(None, "--source-root"),
    adopt_existing: bool = typer.Option(False, "--adopt-existing"),
    preserve_existing_missing: bool = typer.Option(
        False,
        "--preserve-existing-missing",
        help="Preserve and report pre-existing missing dependencies; never allow cycles.",
    ),
):
    if not path and not source_root and not handle and not workspace:
        raise typer.BadParameter("--path or --source-root is required")
    show(
        request(
            ctx,
            "POST",
            "/plan",
            dict(
                node_id=node,
                source=path or source_root,
                target=target_root,
                handle_id=handle,
                workspace=workspace,
                adopt_existing=adopt_existing,
                preserve_existing_missing=preserve_existing_missing,
            ),
        )
    )


@migrate.command()
def run(
    ctx: typer.Context,
    plan_id: str,
    repair_existing: bool = typer.Option(False, "--repair-existing"),
):
    m = request(ctx, "GET", "/" + plan_id)
    show(
        request(
            ctx,
            "POST",
            body=dict(
                plan_id=plan_id,
                plan_digest=m["plan_digest"],
                idempotency_key=m.get("idempotency_key") or plan_id,
                repair_existing=repair_existing,
            ),
        )
    )


@migrate.command()
def status(ctx: typer.Context, migration_id: str):
    show(request(ctx, "GET", "/" + migration_id))


@migrate.command()
def resume(ctx: typer.Context, migration_id: str):
    show(request(ctx, "POST", "/" + migration_id + "/resume"))


@migrate.command()
def cancel(ctx: typer.Context, migration_id: str):
    show(request(ctx, "POST", "/" + migration_id + "/cancel"))


@migrate.command()
def rollback(ctx: typer.Context, migration_id: str):
    show(request(ctx, "POST", "/" + migration_id + "/rollback"))


@migrate.command()
def finalize(
    ctx: typer.Context,
    migration_id: str,
    delete_source: bool = typer.Option(
        False, "--delete-source", help="Explicitly authorize deletion of retained source data."
    ),
):
    show(request(ctx, "POST", "/" + migration_id + "/finalize", dict(delete_source=delete_source)))


@migrate.command()
def serve(
    ctx: typer.Context,
    db_path: Path = typer.Option(..., "--db-path"),
    node: str = typer.Option(..., "--node"),
    source_root: list[Path] = typer.Option(..., "--source-root"),
    gateway: str | None = typer.Option(None, "--gateway"),
    port: int = typer.Option(8830, "--port"),
):
    import uvicorn

    from hololab.gateway.artifact_migrations import maintenance_app

    token_path = ctx.obj["token_file"]
    token_path.parent.mkdir(parents=True, exist_ok=True)
    if not token_path.exists():
        fd = os.open(token_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(secrets.token_urlsafe(32))
    if token_path.is_symlink() or token_path.stat().st_mode & 0o077:
        raise typer.BadParameter("Maintenance token must be a private regular file (0600)")
    token = token_path.read_text().strip()
    if len(token) < 24:
        raise typer.BadParameter("Maintenance token is too short")
    uvicorn.run(
        maintenance_app(
            db_path=db_path, node_id=node, roots=source_root, token=token, gateway_url=gateway
        ),
        host="127.0.0.1",
        port=port,
    )
