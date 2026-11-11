"""Persistent, bounded cache of format-library scan results (never source files)."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from types import ModuleType

from hololab.paths import user_data_dir

log = logging.getLogger(__name__)
_MAX_BYTES = 512 * 1024 * 1024
_MAX_ENTRY_BYTES = 64 * 1024 * 1024
_MAX_ENTRIES = 1024


class SourceChangedError(RuntimeError):
    """A scan overlapped a source update; do not persist a mixed generation."""


@lru_cache(maxsize=4)
def library_identity(library: ModuleType) -> tuple:
    # Include implementation bytes as editable installs may retain their version.
    source = Path(library.__file__)
    sources = (source, source.with_name("colmap_io.py"))
    try:
        release = version("colmap4d")
    except PackageNotFoundError:
        release = "unpackaged"
    return (
        release,
        sys.version_info[:2],
        tuple((str(p.resolve()), hashlib.sha256(p.read_bytes()).hexdigest()) for p in sources),
    )


def _stamp(path: Path) -> tuple:
    try:
        link = path.lstat()
    except FileNotFoundError:
        return (str(path), None)
    resolved = path.resolve()
    try:
        target = resolved.stat()
    except FileNotFoundError:
        target = None

    def fields(stat):
        if stat is None:
            return None
        return (
            stat.st_dev,
            stat.st_ino,
            stat.st_mode,
            stat.st_size,
            stat.st_mtime_ns,
            stat.st_ctime_ns,
        )

    return (str(path), fields(link), str(resolved), fields(target))


def scan_key(path: Path, part: str, parameters: tuple, implementation: tuple) -> str | None:
    """Fingerprint dependencies by stat only; never scan point/pixel contents.

    Track all candidate table locations, including absent files, so adding a BIN
    that takes precedence over TXT (or a new sparse/0 directory) invalidates too.
    ctime/inode also detect replacement and same-size rewrites with restored mtime.
    Metadata includes the pixel tree because its result contains pixel byte sizes.
    If a tree cannot be statted, bypass caching and let the library handle it.
    """
    try:
        root = path.absolute()
        dependencies = [_stamp(root)]
        stems = {
            "points": ("points3D", "points_t"),
            "cameras": ("cameras", "images", "times"),
            "metadata": ("cameras", "images", "times", "points3D", "points_t"),
        }[part]
        for directory in (root, root / "sparse", root / "sparse" / "0"):
            dependencies.append(_stamp(directory))
            # All table headers affect the library's choice of sparse directory.
            for stem in ("cameras", "images", "points3D"):
                for ext in ("bin", "txt"):
                    table = directory / f"{stem}.{ext}"
                    if stem not in stems:
                        dependencies.append((str(table), table.is_file()))
            for stem in stems:
                for ext in ("bin", "txt"):
                    dependencies.append(_stamp(directory / f"{stem}.{ext}"))
            if part != "points":
                dependencies.append(_stamp(directory / "time_meta.json"))
        if part == "metadata":
            pixels = root / "images"
            if not pixels.exists() and root.name == "0" and root.parent.name == "sparse":
                pixels = root.parent.parent / "images"
            elif not pixels.exists() and root.name == "sparse":
                pixels = root.parent / "images"
            dependencies.append(_stamp(pixels))
            if not pixels.is_symlink() and pixels.is_dir():
                dependencies.extend(_stamp(p) for p in sorted(pixels.rglob("*")))
        identity = ("colmap4d-wire-v1", implementation, part, parameters, dependencies)
        return hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
    except (OSError, RuntimeError):
        return None


class FormatPreviewCache:
    """Cache exact JSON bytes with LRU eviction and per-process single flight.

    SQLite transactions coordinate storage/eviction across processes; FULL
    auto-vacuum releases evicted BLOB pages instead of growing the DB forever.
    Only rows in this cache's own database are ever deleted. Source generations
    are checked again after a cache read or scan, before serving/storing bytes.
    """

    def __init__(
        self,
        directory: Path | None = None,
        *,
        max_bytes: int = _MAX_BYTES,
        max_entry_bytes: int = _MAX_ENTRY_BYTES,
        max_entries: int = _MAX_ENTRIES,
    ) -> None:
        self.directory = directory or user_data_dir() / "node" / "format-preview-cache"
        self.max_bytes = max_bytes
        self.max_entry_bytes = max_entry_bytes
        self.max_entries = max_entries
        self._flights = [threading.Lock() for _ in range(32)]
        self._warned = False

    @contextmanager
    def _database(self) -> Iterator[sqlite3.Connection]:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        connection = sqlite3.connect(self.directory / "scans-v1.sqlite3", timeout=2)
        try:
            connection.execute("PRAGMA auto_vacuum=FULL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS scans "
                "(key TEXT PRIMARY KEY, body BLOB NOT NULL, digest TEXT NOT NULL, accessed INTEGER)"
            )
            with connection:
                yield connection
        finally:
            connection.close()

    def _unavailable(self, exc: Exception) -> None:
        if not self._warned:
            self._warned = True
            log.warning("format preview cache unavailable; serving uncached: %s", exc)

    def get(self, key: str) -> bytes | None:
        try:
            with self._database() as db:
                # Start the transaction before reading so an eviction cannot
                # race the LRU touch in another process.
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT body, digest FROM scans WHERE key=?", (key,)).fetchone()
                if row is None:
                    return None
                body, digest = row
                if len(body) > self.max_entry_bytes or hashlib.sha256(body).hexdigest() != digest:
                    db.execute("DELETE FROM scans WHERE key=?", (key,))
                    return None
                db.execute("UPDATE scans SET accessed=? WHERE key=?", (time.time_ns(), key))
                return body
        except (OSError, sqlite3.Error) as exc:
            self._unavailable(exc)
            return None

    def put(self, key: str, body: bytes) -> None:
        size = len(body)
        if size > min(self.max_bytes, self.max_entry_bytes) or self.max_entries < 1:
            return
        try:
            with self._database() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("DELETE FROM scans WHERE key=?", (key,))
                total, count = db.execute(
                    "SELECT coalesce(sum(length(body)), 0), count(*) FROM scans"
                ).fetchone()
                while total + size > self.max_bytes or count >= self.max_entries:
                    oldest, length = db.execute(
                        "SELECT key, length(body) FROM scans ORDER BY accessed, key LIMIT 1"
                    ).fetchone()
                    db.execute("DELETE FROM scans WHERE key=?", (oldest,))
                    total -= length
                    count -= 1
                db.execute(
                    "INSERT INTO scans VALUES (?, ?, ?, ?)",
                    (key, body, hashlib.sha256(body).hexdigest(), time.time_ns()),
                )
        except (OSError, sqlite3.Error) as exc:
            self._unavailable(exc)

    def render(self, fingerprint: Callable[[], str | None], compute: Callable[[], bytes]) -> bytes:
        key = fingerprint()
        if key is None:
            return compute()
        with self._flights[int(key[:8], 16) % len(self._flights)]:
            body = self.get(key)
            if body is not None and fingerprint() == key:
                return body
            body = compute()
            if fingerprint() != key:
                raise SourceChangedError(
                    "Preview source changed during preparation; retry the request"
                )
            self.put(key, body)
            return body
