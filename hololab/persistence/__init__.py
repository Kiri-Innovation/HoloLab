"""Persistence layer — SQLite with WAL, single writer coroutine, append-only events.

See docs/architecture.md#persistence.
"""

from hololab.persistence.db import Database, open_database
from hololab.persistence.migrations import MIGRATIONS, current_schema_version

__all__ = ["MIGRATIONS", "Database", "current_schema_version", "open_database"]
