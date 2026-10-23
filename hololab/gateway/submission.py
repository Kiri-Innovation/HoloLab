"""Durable at-most-once execution submission; retries never create another run.

An interrupted pending request is reported explicitly, not silently executed
again. Configuration parsing uses a context-local token, never global state.
"""

import asyncio
import hashlib
import json
import time
from contextvars import ContextVar

from starlette.responses import JSONResponse, Response

preparation_token: ContextVar[str | None] = ContextVar("preparation_token", default=None)


def install_submission_guard(app):
    locks = {}

    @app.middleware("http")
    async def guard(request, call_next):
        path = request.url.path
        applies = request.method == "POST" and (
            path.endswith("/run") or "/dispatch/" in path or "/rerun-from/" in path
        )
        if not applies:
            return await call_next(request)
        key = request.headers.get("X-Request-ID")
        token = preparation_token.set(request.headers.get("X-Preparation-Token"))
        try:
            if not key:
                return await call_next(request)
            body = await request.body()
            fingerprint = hashlib.sha256(
                path.encode()
                + request.url.query.encode()
                + body
                + (preparation_token.get() or "").encode()
            ).hexdigest()
            lock = locks.setdefault(key, asyncio.Lock())
            async with lock:
                async with (
                    app.state.db.read() as conn,
                    conn.execute(
                        "SELECT request_fingerprint,response_json FROM execution_submissions WHERE request_id=?",
                        (key,),
                    ) as cur,
                ):
                    prior = await cur.fetchone()
                if prior:
                    if prior[0] != fingerprint:
                        return JSONResponse(
                            {"detail": "Request ID was used for another submission"},
                            status_code=409,
                        )
                    if prior[1] is None:
                        return JSONResponse(
                            {
                                "detail": "Submission interrupted or pending; inspect run history before starting a new attempt"
                            },
                            status_code=409,
                        )
                    saved = json.loads(prior[1])
                    return Response(
                        saved["body"], status_code=saved["status"], media_type="application/json"
                    )

                async def claim(conn):
                    await conn.execute(
                        "INSERT INTO execution_submissions VALUES (?, ?, NULL, ?)",
                        (key, fingerprint, time.time()),
                    )

                await app.state.db.write(claim)
                response = await call_next(request)
                payload = b"".join([chunk async for chunk in response.body_iterator])
                saved = json.dumps({"status": response.status_code, "body": payload.decode()})

                async def finish(conn):
                    await conn.execute(
                        "UPDATE execution_submissions SET response_json=? WHERE request_id=?",
                        (saved, key),
                    )

                await app.state.db.write(finish)
                return Response(
                    payload, status_code=response.status_code, headers=dict(response.headers)
                )
        finally:
            preparation_token.reset(token)
