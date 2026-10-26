"""JPEG XMP preservation regression for image-undistort@0.5.2."""

from __future__ import annotations

import importlib.util
from pathlib import Path

PACK = Path(__file__).resolve().parent.parent / "packs/image-undistort@0.5.2"


def _module():
    spec = importlib.util.spec_from_file_location(
        "image_undistort_jpeg_metadata", PACK / "jpeg_metadata.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _segment(marker: int, payload: bytes) -> bytes:
    return b"\xff" + bytes([marker]) + (len(payload) + 2).to_bytes(2, "big") + payload


def _jpeg(*segments: bytes, scan: bytes = b"encoded-scan\xff\x00bytes\xff\xd9") -> bytes:
    return b"\xff\xd8" + b"".join(segments) + b"\xff\xda\x00\x02" + scan


def test_transplant_copies_standard_and_extended_xmp_but_not_exif() -> None:
    meta = _module()
    standard = _segment(0xE1, meta.STANDARD_XMP_HEADER + b"<x:xmpmeta/>")
    extended = _segment(0xE1, meta.EXTENDED_XMP_HEADER + b"guid-and-chunk")
    exif = _segment(0xE1, b"Exif\x00\x00orientation-must-not-copy")
    source = _jpeg(exif, standard, extended)
    destination = _jpeg(_segment(0xE0, b"JFIF\x00"), scan=b"destination-scan\xff\xd9")

    result = meta.transplant_xmp_app1_bytes(source, destination)

    assert meta.extract_xmp_app1_segments(result) == [standard, extended]
    assert b"orientation-must-not-copy" not in result
    assert result[result.index(b"\xff\xda") :] == destination[destination.index(b"\xff\xda") :]


def test_transplant_replaces_existing_destination_xmp() -> None:
    meta = _module()
    old = _segment(0xE1, meta.STANDARD_XMP_HEADER + b"old")
    new = _segment(0xE1, meta.STANDARD_XMP_HEADER + b"new")

    result = meta.transplant_xmp_app1_bytes(_jpeg(new), _jpeg(old))

    assert meta.extract_xmp_app1_segments(result) == [new]
    assert old not in result
