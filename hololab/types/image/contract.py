from pathlib import Path
from typing import Any

from hololab.manifest.data_contract import ContractError

REMOVED_IMAGE_TAGS = frozenset({"image_sequence", "frame_sequence"})


def _check_image_declaration(spec: Any) -> None:
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


def _check_image_leaf(current: Path, *, label: str) -> None:
    if not current.is_file():
        raise ContractError(f"{label}: expected one image file, got directory/non-file: {current}")
    check_image_header(current, label=label)


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
