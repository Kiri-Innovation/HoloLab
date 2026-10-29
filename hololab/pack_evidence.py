"""Content identity independent of registry publication metadata."""

from __future__ import annotations

from hololab.bundled import is_builtin


def verified_code_sha256(pack: dict | None) -> str | None:
    if not pack:
        return None
    if is_builtin(pack.get("name", "")):
        if pack.get("origin") != "bundled":
            return None
        return (pack.get("bundled_identity") or {}).get("code_sha256")
    return (pack.get("publication") or {}).get("code_sha256")
