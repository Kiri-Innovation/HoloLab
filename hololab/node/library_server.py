"""Capability-scoped read-only peer endpoint on the existing node file server."""

from __future__ import annotations

import asyncio
import os
import re

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from hololab.library_transport import open_library_file, verify_capability


def mount_library_reads(app, root, token_provider):
    @app.get("/_library/content")
    async def content(request: Request):
        if root is None or token_provider is None:
            raise HTTPException(404, "Library serving is not configured")
        try:
            secret = token_provider()
            if not secret:
                raise ValueError("Node identity unavailable")
            payload = verify_capability(secret, request.headers.get("x-library-capability", ""))
        except (ValueError, KeyError, TypeError):
            raise HTTPException(403, "Invalid or expired Library capability") from None
        etag = f'"{payload["sha256"]}"'
        if request.headers.get("if-range", etag) != etag:
            raise HTTPException(412, "Source version mismatch")
        match = re.fullmatch(r"bytes=(\d+)-(\d+)", request.headers.get("range", ""))
        if not match:
            raise HTTPException(416, "A bounded Range is required")
        start, end = map(int, match.groups())
        if not 0 <= start <= end < payload["size"] or end - start + 1 > 8 * 1024**2:
            raise HTTPException(416, "Invalid Range")
        try:
            source = open_library_file(root, payload["path"])
        except (OSError, ValueError):
            raise HTTPException(404, "Library source unavailable") from None
        if os.fstat(source.fileno()).st_size != payload["size"]:
            source.close()
            raise HTTPException(409, "Library source size changed")

        async def stream():
            try:
                source.seek(start)
                remaining = end - start + 1
                while remaining:
                    block = await asyncio.to_thread(source.read, min(65536, remaining))
                    if not block:
                        raise OSError("Source truncated")
                    remaining -= len(block)
                    yield block
            finally:
                source.close()

        return StreamingResponse(
            stream(),
            status_code=206,
            media_type="application/octet-stream",
            headers={
                "Content-Range": f"bytes {start}-{end}/{payload['size']}",
                "Content-Length": str(end - start + 1),
                "ETag": etag,
                "Accept-Ranges": "bytes",
                "Cache-Control": "no-store",
            },
        )
