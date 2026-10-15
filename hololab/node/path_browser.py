"""Read-only node filesystem metadata. No shell, file reads or writes.

Symlinks are labelled and followed for navigation (node OS permissions apply).
Pages use scan-order offsets, not a snapshot: refresh after directory changes.
Only matching entries are stat'ed; each page scans at most 4096 new entries.
"""

import itertools
import os
from pathlib import Path


def list_path(path: str, prefix: str = "", offset: int = 0, limit: int = 100) -> dict:
    if not 1 <= limit <= 200 or not 0 <= offset <= 1_000_000:
        return {"error": "Invalid page bounds", "status": 400}
    if "\0" in path or "\0" in prefix or "/" in prefix or len(path) > 4096:
        return {"error": "Invalid path or filename prefix", "status": 400}
    try:
        directory = Path(path).expanduser()
    except RuntimeError:
        return {"error": "Cannot resolve this user home directory", "status": 400}
    if not directory.is_absolute():
        return {"error": "Use an absolute path on the compute node", "status": 400}
    try:
        directory = directory.resolve(strict=True)
        rows = []
        consumed = offset
        more = False
        with os.scandir(directory) as iterator:
            page = itertools.islice(iterator, offset, None)
            for entry in page:
                consumed += 1
                if entry.name.startswith(prefix):
                    try:
                        stat = entry.stat(follow_symlinks=True)
                        kind = (
                            "directory"
                            if entry.is_dir()
                            else "file"
                            if entry.is_file()
                            else "other"
                        )
                        rows.append(
                            {
                                "name": entry.name,
                                "path": str(directory / entry.name),
                                "kind": kind,
                                "symlink": entry.is_symlink(),
                                "size": stat.st_size,
                                "modified_ts": stat.st_mtime,
                            }
                        )
                    except OSError:
                        rows.append(
                            {
                                "name": entry.name,
                                "path": str(directory / entry.name),
                                "kind": "unavailable",
                                "symlink": entry.is_symlink(),
                                "size": None,
                                "modified_ts": None,
                            }
                        )
                if len(rows) >= limit or consumed - offset >= 4096:
                    more = next(page, None) is not None
                    break
        rows.sort(key=lambda row: (row["kind"] != "directory", row["name"]))
        return {
            "path": str(directory),
            "parent": str(directory.parent),
            "entries": rows,
            "next_offset": consumed if more else None,
            "error": None,
        }
    except FileNotFoundError:
        return {"error": "Path does not exist on this compute node", "status": 404}
    except NotADirectoryError:
        return {"error": "Expected a directory", "status": 400}
    except PermissionError:
        return {"error": "Permission denied on this compute node", "status": 403}
    except (OSError, RuntimeError) as exc:
        return {"error": f"Cannot list directory: {exc}", "status": 400}
