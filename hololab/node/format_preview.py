"""Read-only format-library adapters with cached scan results; no format parsing."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from dataclasses import asdict
from importlib import import_module
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, Response

from hololab.node.format_preview_cache import (
    FormatPreviewCache,
    SourceChangedError,
    library_identity,
    scan_key,
)


def mount_format_previews(app: FastAPI, resolve_path: Callable[[str], Path | None]) -> None:
    slots = asyncio.Semaphore(2)
    cache = FormatPreviewCache()

    @app.get("/_format/colmap4d/{sub:path}")
    async def colmap4d_preview(
        sub: str,
        part: Literal["metadata", "cameras", "points"] = "metadata",
        budget: int = Query(default=200_000, ge=1, le=200_000),
        offset: int = Query(default=0, ge=0, le=1_000_000_000),
        limit: int | None = Query(default=None, ge=1, le=200_000),
    ):
        if not sub or Path(sub).is_absolute() or ".." in sub.split("/") or "\\" in sub:
            raise HTTPException(403, "path outside workspace")
        path = resolve_path(sub)
        if path is None or not path.is_dir():
            raise HTTPException(404, "preview directory not found")
        if part == "cameras" and limit is not None and limit > 100_000:
            raise HTTPException(422, "camera page limit exceeds 100000")
        try:
            library = import_module("colmap4d.preview")
        except ImportError as exc:
            raise HTTPException(
                503, "Install hololab[preview-colmap4d] on this compute node"
            ) from exc

        def call():
            def compute():
                if part == "metadata":
                    result = asdict(library.inspect_components(path))
                else:
                    result = asdict(
                        library.preview_files(
                            path,
                            part=part,
                            budget=budget,
                            offset=offset,
                            limit=limit,
                        )
                    )
                    result["files"] = [
                        {"name": name, "base64": base64.b64encode(data).decode("ascii")}
                        for name, data in result["files"].items()
                    ]
                return JSONResponse({"format": "colmap4d", "part": part, "result": result}).body

            try:
                implementation = library_identity(library)
            except OSError:
                # An unreadable library source must not break a working import.
                return compute()
            return cache.render(
                lambda: scan_key(path, part, (budget, offset, limit), implementation), compute
            )

        try:
            async with slots:
                body = await asyncio.to_thread(call)
        except SourceChangedError as exc:
            raise HTTPException(409, str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return Response(body, media_type="application/json")
