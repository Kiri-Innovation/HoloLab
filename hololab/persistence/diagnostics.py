"""Opt-in latency diagnostics (HOLOLAB_LATENCY_DIAGNOSTICS=1).

No SQL, query parameters, response bodies or local variable values are logged.
DB timings separate worker queueing, execution and event-loop resumption.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import threading
import time
from collections import Counter
from contextvars import ContextVar
from typing import Any

from hololab.logging import get_logger

log = get_logger("latency")
current: ContextVar[dict[str, Any] | None] = ContextVar("latency", default=None)


def enabled() -> bool:
    return os.environ.get("HOLOLAB_LATENCY_DIAGNOSTICS") == "1"


@contextlib.contextmanager
def phase(name: str):
    metrics = current.get()
    if metrics is None:
        yield
        return
    start = time.perf_counter()
    try:
        yield
    finally:
        metrics.setdefault("phases_ms", {})[name] = round((time.perf_counter() - start) * 1000, 3)


def instrument_reader(conn) -> None:
    # aiosqlite has no public queue timing hook. Keep this private hook
    # isolated, opt-in, and covered by a real-connection test.
    original = conn._execute

    async def execute(fn, *args, **kwargs):
        metrics = current.get()
        if metrics is None:
            return await original(fn, *args, **kwargs)
        submitted = time.perf_counter()
        started = finished = submitted

        def measured():
            nonlocal started, finished
            started = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                finished = time.perf_counter()

        try:
            return await original(measured)
        finally:
            resumed = time.perf_counter()
            metrics["db_ops"] = metrics.get("db_ops", 0) + 1
            for key, seconds in (
                ("db_queue_ms", started - submitted),
                ("db_work_ms", finished - started),
                ("db_resume_ms", resumed - finished),
            ):
                metrics[key] = metrics.get(key, 0.0) + seconds * 1000

    conn._execute = execute


class LatencyMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        metrics: dict[str, Any] = {}
        token = current.set(metrics)
        start = time.perf_counter()
        try:
            await self.app(scope, receive, send)
        finally:
            current.reset(token)
            elapsed = (time.perf_counter() - start) * 1000
            if scope["path"] == "/api/resolve" or elapsed >= 200:
                metrics = {
                    k: round(v, 3) if isinstance(v, float) else v for k, v in metrics.items()
                }
                log.info(
                    "request latency", path=scope["path"], total_ms=round(elapsed, 3), **metrics
                )


async def monitor_loop() -> None:
    """20Hz lag probe; aggregate the loop thread's stacks every five seconds."""
    stop = threading.Event()
    loop_thread = threading.get_ident()

    def sample():
        counts: Counter[str] = Counter()
        last = time.perf_counter()
        while not stop.wait(0.05):
            frame = sys._current_frames().get(loop_thread)
            stack = []
            while frame is not None:
                if "/hololab/" in frame.f_code.co_filename:
                    stack.append(f"{frame.f_code.co_name}:{frame.f_lineno}")
                frame = frame.f_back
            if stack:
                counts[" <- ".join(stack[:8])] += 1
            if time.perf_counter() - last >= 5:
                if counts:
                    log.info("loop stack samples", stacks=counts.most_common(5))
                counts.clear()
                last = time.perf_counter()

    thread = threading.Thread(target=sample, name="latency-sampler", daemon=True)
    thread.start()
    try:
        while True:
            start = time.perf_counter()
            await asyncio.sleep(0.05)
            lag = (time.perf_counter() - start - 0.05) * 1000
            if lag >= 50:
                log.info("event loop lag", lag_ms=round(lag, 3))
    finally:
        stop.set()
        await asyncio.to_thread(thread.join, 1)
