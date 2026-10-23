"""Package identity syntax and legacy aliases; never rewrites recorded identities."""

from __future__ import annotations

import re

DEFAULT_SCOPE = "kiri"
_SEGMENT = re.compile(r"[a-z0-9][a-z0-9_-]*", re.ASCII)
_LEGACY = re.compile(r"[a-z0-9_-]+", re.ASCII)
_VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\+?", re.ASCII)
INTRINSICS = frozenset(
    {"@builtin/array-assemble", "@builtin/constant-float-array", "@builtin/constant-vec3-array"}
)


def validate_name(name: str, *, allow_legacy: bool = True) -> str:
    if not isinstance(name, str):
        raise ValueError("package name must be a string")
    parts = name.split("/")
    if len(parts) == 1 and allow_legacy and _LEGACY.fullmatch(name):
        return name
    if (
        len(parts) != 2
        or not all(_SEGMENT.fullmatch(p) for p in parts)
        or len(parts[0]) > 63
        or len(parts[1]) > 127
    ):
        raise ValueError(
            "package name must be lowercase scope/name (ASCII letters, digits, _ or -)"
        )
    return name


def validate_version(version: str) -> str:
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise ValueError("package version must be exact MAJOR.MINOR.PATCH with optional trailing +")
    return version


def validate_identity(name: str, version: str, *, intrinsic: bool = False) -> tuple[str, str]:
    if intrinsic:
        if name not in INTRINSICS or version != "1":
            raise ValueError("reserved intrinsic identity")
    else:
        validate_name(name)
        validate_version(version)
    return name, version


def canonical_name(name: str | None) -> str | None:
    # Comparison is deliberately tolerant of old records; validate on ingress.
    return f"{DEFAULT_SCOPE}/{name}" if name and "/" not in name else name


def package_key(name: str | None, version: str | None) -> tuple[str | None, str | None]:
    return canonical_name(name), version


def same_package(
    name: str | None, version: str | None, other: str | None, other_version: str | None
) -> bool:
    return package_key(name, version) == package_key(other, other_version)


def parse_spec(spec: str) -> tuple[str, str]:
    if spec.count("@") != 1:
        raise ValueError("package reference must be scope/name@version")
    return validate_identity(*spec.split("@"))


def is_dev(version: str) -> bool:
    validate_version(version)
    return version.endswith("+")


def offered_identity(packs, name: str, version: str) -> tuple[str, str]:
    """Return the selected node's spelling, rejecting ambiguous aliases."""
    matches = {(p.name, p.version) for p in packs if same_package(p.name, p.version, name, version)}
    if len(matches) > 1:
        raise ValueError(f"ambiguous package aliases for {canonical_name(name)}@{version}")
    if not matches:
        raise ValueError(f"package {name}@{version} is not offered by this node")
    return next(iter(matches))


def identity_aliases(name: str, version: str) -> set[tuple[str, str]]:
    canonical = canonical_name(name)
    result = {(name, version), (canonical, version)}
    if canonical.startswith(DEFAULT_SCOPE + "/"):
        result.add((canonical.split("/", 1)[1], version))
    return result


def alias_index(entries: dict) -> dict:
    return {
        alias: value
        for (name, version), value in entries.items()
        for alias in identity_aliases(name, version)
    }


def validate_graph_identities(graph) -> None:
    for node in graph.nodes:
        if node.kind == "algorithm":
            validate_identity(node.algorithm_name, node.algorithm_version)


def installed_key(packs: dict, name: str, version: str) -> tuple[str, str]:
    matches = [key for key in packs if same_package(*key, name, version)]
    if len(matches) > 1:
        raise ValueError(f"ambiguous package aliases for {canonical_name(name)}@{version}")
    if not matches:
        raise ValueError(f"package {name}@{version} is not installed")
    return matches[0]
