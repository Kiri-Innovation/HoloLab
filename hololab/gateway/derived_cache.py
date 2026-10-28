"""Bounded gateway cache and single-flight coordination for derived previews."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections import OrderedDict
from dataclasses import dataclass

_TOTAL_BYTES = 128 * 1024 * 1024
_PER_NODE_BYTES = 32 * 1024 * 1024
_MAX_OBJECT_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class CachedPreview:
    body: bytes
    headers: dict[str, str]
    expires_at: float
    node_id: str


class DerivedPreviewCache:
    """Memory-bounded LRU cache keyed by source ETag plus a single-flight map."""

    def __init__(self) -> None:
        self._entries: OrderedDict[str, CachedPreview] = OrderedDict()
        self._index: dict[str, str] = {}
        self._inflight: dict[str, asyncio.Future[CachedPreview | None]] = {}
        self._bytes = 0
        self._node_bytes: dict[str, int] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def basic_key(node_id: str, path: str, query: str) -> str:
        return "\0".join((node_id, path, query))

    async def get_or_reserve(
        self, node_id: str, path: str, query: str
    ) -> tuple[CachedPreview | None, asyncio.Future[CachedPreview | None] | None, bool]:
        """Return (hit, future, owner). Non-owners wait on ``future``."""
        basic = self.basic_key(node_id, path, query)
        async with self._lock:
            key = self._index.get(basic)
            entry = self._entries.get(key) if key else None
            if entry is not None and entry.expires_at > time.monotonic():
                self._entries.move_to_end(key)
                return entry, None, False
            if key:
                self._drop(key)
                self._index.pop(basic, None)
            pending = self._inflight.get(basic)
            if pending is not None:
                return None, pending, False
            pending = asyncio.get_running_loop().create_future()
            self._inflight[basic] = pending
            return None, pending, True

    async def finish(
        self,
        node_id: str,
        path: str,
        query: str,
        headers: dict[str, str],
        body: bytes | None,
        ttl_s: float,
    ) -> None:
        basic = self.basic_key(node_id, path, query)
        async with self._lock:
            future = self._inflight.pop(basic, None)
            entry: CachedPreview | None = None
            etag = headers.get("etag")
            if body is not None and etag and len(body) <= _MAX_OBJECT_BYTES:
                key = hashlib.sha256((basic + "\0" + etag).encode()).hexdigest()
                entry = CachedPreview(
                    body=body,
                    headers=dict(headers),
                    expires_at=time.monotonic() + ttl_s,
                    node_id=node_id,
                )
                old = self._index.get(basic)
                if old:
                    self._drop(old)
                self._entries[key] = entry
                self._index[basic] = key
                self._bytes += len(body)
                self._node_bytes[node_id] = self._node_bytes.get(node_id, 0) + len(body)
                self._evict()
            if future is not None and not future.done():
                future.set_result(entry)

    async def fail(self, node_id: str, path: str, query: str) -> None:
        basic = self.basic_key(node_id, path, query)
        async with self._lock:
            future = self._inflight.pop(basic, None)
            if future is not None and not future.done():
                future.set_result(None)

    def _drop(self, key: str) -> None:
        entry = self._entries.pop(key, None)
        if entry is None:
            return
        self._bytes -= len(entry.body)
        self._node_bytes[entry.node_id] = self._node_bytes.get(entry.node_id, 0) - len(entry.body)

    def _evict(self) -> None:
        while self._entries and (
            self._bytes > _TOTAL_BYTES
            or any(size > _PER_NODE_BYTES for size in self._node_bytes.values())
        ):
            key, _ = self._entries.popitem(last=False)
            # put it back solely so _drop owns accounting in one place
            self._entries[key] = _
            self._drop(key)
