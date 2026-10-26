"""Losslessly transplant XMP APP1 segments between JPEG byte streams.

Only JPEG header segments before SOS are inspected. The destination's encoded
scan data is copied verbatim, so attaching metadata cannot alter image pixels.
EXIF APP1 is deliberately not copied: an input Orientation tag must not be
re-applied after undistortion has already produced display-oriented pixels.
"""

from __future__ import annotations

from pathlib import Path

SOI = b"\xff\xd8"
SOS = 0xDA
EOI = 0xD9
APP1 = 0xE1
STANDARD_XMP_HEADER = b"http://ns.adobe.com/xap/1.0/\x00"
EXTENDED_XMP_HEADER = b"http://ns.adobe.com/xmp/extension/\x00"
XMP_HEADERS = (STANDARD_XMP_HEADER, EXTENDED_XMP_HEADER)


def _is_xmp(payload: bytes) -> bool:
    return payload.startswith(XMP_HEADERS)


def _walk_header(jpeg: bytes):
    """Yield ``(marker, payload, raw_segment)`` through the destination SOS."""
    if not jpeg.startswith(SOI):
        raise ValueError("not a JPEG (missing SOI marker)")
    pos = len(SOI)
    while pos < len(jpeg):
        marker_start = pos
        if jpeg[pos] != 0xFF:
            raise ValueError("invalid JPEG marker alignment before SOS")
        while pos < len(jpeg) and jpeg[pos] == 0xFF:
            pos += 1
        if pos >= len(jpeg):
            raise ValueError("truncated JPEG marker")
        marker = jpeg[pos]
        pos += 1
        if marker == SOS:
            yield marker, b"", jpeg[marker_start:]
            return
        if marker == EOI:
            raise ValueError("JPEG has no SOS marker")
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            yield marker, b"", jpeg[marker_start:pos]
            continue
        if pos + 2 > len(jpeg):
            raise ValueError("truncated JPEG segment length")
        segment_len = int.from_bytes(jpeg[pos : pos + 2], "big")
        if segment_len < 2 or pos + segment_len > len(jpeg):
            raise ValueError("invalid JPEG segment length")
        end = pos + segment_len
        yield marker, jpeg[pos + 2 : end], jpeg[marker_start:end]
        pos = end
    raise ValueError("JPEG has no SOS marker")


def extract_xmp_app1_segments(jpeg: bytes) -> list[bytes]:
    """Return raw standard and Extended XMP APP1 segments in source order."""
    segments = []
    for marker, payload, raw in _walk_header(jpeg):
        if marker == SOS:
            break
        if marker == APP1 and _is_xmp(payload):
            # Each segment came from a valid 16-bit JPEG segment length. Large
            # packets represented as Adobe Extended XMP therefore remain split
            # exactly as authored instead of being recombined past 64 KiB.
            segments.append(raw)
    return segments


def transplant_xmp_app1_bytes(source: bytes, destination: bytes) -> bytes:
    """Replace destination XMP with source XMP without touching scan bytes."""
    source_segments = extract_xmp_app1_segments(source)
    kept = [SOI]
    for marker, payload, raw in _walk_header(destination):
        if marker == SOS:
            kept.extend(source_segments)
            kept.append(raw)
            return b"".join(kept)
        if marker != APP1 or not _is_xmp(payload):
            kept.append(raw)
    raise ValueError("JPEG has no SOS marker")


def transplant_xmp_app1(source: Path, destination: Path) -> int:
    """Atomically transplant XMP APP1 segments; return the segment count."""
    source_bytes = Path(source).read_bytes()
    destination = Path(destination)
    rewritten = transplant_xmp_app1_bytes(source_bytes, destination.read_bytes())
    tmp = destination.with_name(f".{destination.name}.xmp-tmp")
    tmp.write_bytes(rewritten)
    tmp.replace(destination)
    return len(extract_xmp_app1_segments(source_bytes))
