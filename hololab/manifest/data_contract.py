"""Generic invocation traversal and type-owned contract callbacks.

Collections remain explicit directory layers; dot-prefixed metadata is not an
element. Existing type checks are loaded from declarations without introducing
new checks or changing their error messages.
"""

from pathlib import Path
from typing import Any


def retired_tags() -> frozenset[str]:
    from hololab.type_registry import registry

    registry.refresh()
    return frozenset(
        tag
        for definition in registry.definitions.values()
        for tag in definition.declaration.get("retired_tags", [])
    )


class ContractError(ValueError):
    pass


def check_declaration(spec: Any) -> None:
    from hololab.type_registry import registry

    registry.refresh()
    for definition in registry.definitions.values():
        if definition.declaration.get("check_declaration"):
            definition.call("check_declaration", spec)


def validate_path(path: Path, spec: Any, *, label: str, params: dict | None = None) -> None:
    """Check every image leaf, not just the first element or a summary count.

    Opaque tags retain their own bundle formats. `any` is explicitly unconstrained,
    rather than guessed to be an image. The path must still exist.
    """
    variants = getattr(spec, "any_of", None) or []
    if variants:
        failures = []
        for variant in variants:
            try:
                validate_path(path, variant, label=label, params=params)
                return
            except ContractError as exc:
                failures.append(str(exc))
        raise ContractError(
            f"{label}: value matches none of the any_of variants: " + "; ".join(failures)
        )
    if not path.exists():
        raise ContractError(f"{label}: missing input/output path: {path}")
    from hololab.type_registry import registry

    leaf_checks = [
        definition
        for tag in spec.tags
        if (definition := registry.get(tag)) is not None
        and definition.declaration.get("validate_leaf")
    ]
    depth = max(1, len(spec.dim_labels)) if spec.arrayed else 0
    key = getattr(spec, "dim_labels_from", None)
    if key and params and isinstance(params.get(key), list):
        depth = len(params[key])

    def visit(current: Path, remaining: int) -> None:
        if not current.exists():
            raise ContractError(f"{label}: missing array element or broken link: {current}")
        if remaining:
            if not current.is_dir():
                raise ContractError(
                    f"{label}: expected array directory ({remaining} layers), got {current}"
                )
            for child in sorted(current.iterdir()):
                if not child.name.startswith("."):
                    visit(child, remaining - 1)
            return
        for definition in leaf_checks:
            definition.call("validate_leaf", current, label=label)

    try:
        # Depth-first in sorted order, so the first failure reported is the
        # first invalid leaf in traversal order.
        visit(path, depth)
    except OSError as exc:
        raise ContractError(f"{label}: cannot validate {path}: {exc}") from exc


def validate_ports(
    paths: dict[str, str], specs: dict, *, direction: str, params: dict | None = None
) -> None:
    for name, spec in specs.items():
        if name not in paths:
            if direction == "input" and not spec.required:
                continue
            raise ContractError(f"{direction}.{name}: required path missing")
        validate_path(Path(paths[name]), spec, label=f"{direction}.{name}", params=params)
