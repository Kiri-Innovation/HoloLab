"""Concrete invocation contracts. No algorithm inference and no sampling.

Image leaves are individual image files. Collections are explicit directory
layers; dot-prefixed framework metadata is not an element. Symbolic links are
allowed (array utilities use them), but every referenced leaf is checked.

An image leaf is checked by ``stat`` and its header only (format, single frame,
positive width and height); pixel data is not decoded. The framework trusts the
node's pack output beyond that, so a truncated or corrupted pixel body passes
here and surfaces in the algorithm that reads it.
"""

from pathlib import Path
from typing import Any

REMOVED_IMAGE_TAGS = frozenset({"image_sequence", "frame_sequence"})


class ContractError(ValueError):
    pass


def check_declaration(spec: Any) -> None:
    removed = REMOVED_IMAGE_TAGS.intersection(spec.tags)
    if removed:
        raise ValueError(
            f"removed image type(s): {sorted(removed)}; declare image file or arrayed<image>"
        )
    if "image" not in spec.tags:
        return
    if not spec.arrayed and spec.dim_labels:
        raise ValueError("image is one file, with no content dimensions; use arrayed: true")
    required = "dir" if spec.arrayed else "file"
    if spec.storage.value != required:
        raise ValueError(f"image contract requires storage: {required}")
    if getattr(spec, "content_dims_from", None):
        raise ValueError("image has no intrinsic content dimensions")


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
    image_leaf = "image" in spec.tags
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
        if not image_leaf:
            # Opaque bundle contents belong to their tag's validator. `any`
            # can be a file, a bundle, or another collection at this leaf.
            return
        if not current.is_file():
            raise ContractError(
                f"{label}: expected one image file, got directory/non-file: {current}"
            )
        check_image_header(current, label=label)

    try:
        # Depth-first in sorted order, so the first failure reported is the
        # first invalid leaf in traversal order.
        visit(path, depth)
    except OSError as exc:
        raise ContractError(f"{label}: cannot validate {path}: {exc}") from exc


def check_image_header(path: Path, *, label: str) -> tuple[int, int]:
    """Non-empty file whose header is a single-frame image of positive size.

    Reads the header only (``Image.open`` is lazy); the ``with`` block closes the
    file. Returns ``(width, height)``.
    """
    from PIL import Image

    try:
        if path.stat().st_size == 0:
            raise ValueError("empty file")
        with Image.open(path) as img:
            if getattr(img, "n_frames", 1) != 1:
                raise ValueError("multi-frame image is not a single image")
            width, height = img.size
        if width <= 0 or height <= 0:
            raise ValueError(f"invalid image size {width}x{height}")
    except Exception as exc:
        raise ContractError(f"{label}: invalid image file {path}: {exc}") from exc
    return width, height


def validate_ports(
    paths: dict[str, str], specs: dict, *, direction: str, params: dict | None = None
) -> None:
    for name, spec in specs.items():
        if name not in paths:
            if direction == "input" and not spec.required:
                continue
            raise ContractError(f"{direction}.{name}: required path missing")
        validate_path(Path(paths[name]), spec, label=f"{direction}.{name}", params=params)
