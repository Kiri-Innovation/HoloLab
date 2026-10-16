"""Scoped peer capabilities and no-follow reads for immutable Library content."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import stat
import time
from pathlib import Path


def capability(secret: str, *, path: str, sha256: str, size: int) -> str:
    raw = json.dumps(
        {"path": path, "sha256": sha256, "size": size, "expires": time.time() + 180},
        separators=(",", ":"),
    ).encode()
    encoded = base64.urlsafe_b64encode(raw).decode()
    return encoded + "." + hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()


def verify_capability(secret: str, token: str) -> dict:
    encoded, signature = token.rsplit(".", 1)
    expected = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("Invalid peer capability")
    payload = json.loads(base64.urlsafe_b64decode(encoded))
    if payload["expires"] <= time.time():
        raise ValueError("Expired peer capability")
    return payload


def open_library_file(root: Path, relative_path: str):
    parts = relative_path.split("/")
    if any(
        not p
        or p in {".", ".."}
        or "\\" in p
        or ":" in p
        or "\x00" in p
        or p.casefold().startswith(".hololab")
        for p in parts
    ):
        raise ValueError("Invalid library path")
    fd = os.open(root.expanduser().resolve(), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        result = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
    finally:
        os.close(fd)
    if not stat.S_ISREG(os.fstat(result).st_mode):
        os.close(result)
        raise ValueError("Library content is not a regular file")
    return os.fdopen(result, "rb")
