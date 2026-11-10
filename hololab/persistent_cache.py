"""Small SQLite-backed cache used across process restarts.

The cache deliberately stores a caller supplied fingerprint alongside the
payload.  A row is never a validity decision: callers must calculate their
current (cheap) witness before accepting it.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

DDL = """
CREATE TABLE IF NOT EXISTS persistent_cache (
    cache_kind TEXT NOT NULL,
    cache_key TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_ts REAL NOT NULL,
    verified_ts REAL NOT NULL,
    PRIMARY KEY (cache_kind, cache_key)
)
"""


class PersistentCache:
    """Synchronous, short-lived SQLite connections safe between processes."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=1)
        conn.execute("PRAGMA busy_timeout=1000")
        conn.execute(DDL)
        return conn

    def get(self, kind: str, key: str, fingerprint: str) -> Any | None:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT payload_json FROM persistent_cache "
                    "WHERE cache_kind=? AND cache_key=? AND fingerprint=?",
                    (kind, key, fingerprint),
                ).fetchone()
                if row is None:
                    return None
                now = time.time()
                conn.execute(
                    "UPDATE persistent_cache SET verified_ts=? WHERE cache_kind=? AND cache_key=?",
                    (now, kind, key),
                )
                return json.loads(row[0])
        except (OSError, sqlite3.Error, ValueError):
            return None

    def get_any(self, kind: str, key: str) -> Any | None:
        """Read an untrusted candidate; caller must calculate its witness."""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT payload_json FROM persistent_cache WHERE cache_kind=? AND cache_key=?",
                    (kind, key),
                ).fetchone()
                return json.loads(row[0]) if row else None
        except (OSError, sqlite3.Error, ValueError):
            return None

    def put(self, kind: str, key: str, fingerprint: str, payload: Any) -> None:
        try:
            now = time.time()
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO persistent_cache(cache_kind,cache_key,fingerprint,payload_json,created_ts,verified_ts) "
                    "VALUES(?,?,?,?,?,?) ON CONFLICT(cache_kind,cache_key) DO UPDATE SET "
                    "fingerprint=excluded.fingerprint,payload_json=excluded.payload_json,verified_ts=excluded.verified_ts",
                    (kind, key, fingerprint, encoded, now, now),
                )
        except (OSError, sqlite3.Error, TypeError, ValueError):
            pass
