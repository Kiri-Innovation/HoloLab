"""Lossless metric optical-axis depth and optional frame provenance."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from hololab.array_positions import encode_position, enumerate_array_entries
from hololab.typed_values import HType

if TYPE_CHECKING:
    import numpy as np

FRAME_ATTRIBUTE = "hololabFrame"


@dataclass(frozen=True)
class FrameMetadata:
    source_id: str
    frame_id: str
    timestamp_ns: int
    clock_domain: str

    def validate(self):
        for value in (self.source_id, self.frame_id, self.clock_domain):
            if not isinstance(value, str) or not value.strip() or "\0" in value:
                raise ValueError("invalid depth frame identity or clock domain")
        if type(self.timestamp_ns) is not int or not -(2**63) <= self.timestamp_ns < 2**63:
            raise ValueError("depth frame timestamp must be signed int64 nanoseconds")


def read_frame_metadata(attribute):
    def unique_fields(fields):
        data = dict(fields)
        if len(data) != len(fields):
            raise ValueError("duplicate depth frame metadata field")
        return data

    try:
        if not isinstance(attribute, str):
            raise TypeError("expected an EXR string attribute")
        frame = FrameMetadata(**json.loads(attribute, object_pairs_hook=unique_fields))
        frame.validate()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {FRAME_ATTRIBUTE} EXR attribute: {exc}") from exc
    return frame


def check_port(spec, arrayed):
    if spec is None:
        return
    if (tuple(spec.tags), spec.arrayed, spec.storage.value, tuple(spec.dim_labels)) != (
        ("depth_map",),
        arrayed,
        "dir" if arrayed else "file",
        ("frame",) if arrayed else (),
    ):
        raise ValueError("expected a depth_map file or a depth_map[frame] directory")


def read_exr_header(exr):
    import numpy as np
    import OpenEXR

    if len(exr.parts) != 1:
        raise ValueError("expected one EXR part")
    header = exr.header()
    origin, end = header["dataWindow"]
    channels = header["channels"]
    if (
        header["type"] != OpenEXR.scanlineimage
        or header["compression"] != OpenEXR.ZIP_COMPRESSION
        or len(channels) != 1
        or channels[0].name != "Z"
        or (channels[0].xSampling, channels[0].ySampling) != (1, 1)
        or not (origin == 0).all()
        or not (end >= origin).all()
        or not np.array_equal(header["displayWindow"], (origin, end))
    ):
        raise ValueError("expected full-window, origin-zero, single-Z scanline ZIP OpenEXR")
    frame = read_frame_metadata(header[FRAME_ATTRIBUTE]) if FRAME_ATTRIBUTE in header else None
    return frame, tuple(int(n) + 1 for n in end[::-1])


class DepthCodec(HType):
    @classmethod
    def read(cls, path, *, spec, io):
        if spec is not None and spec.arrayed:
            check_port(spec, True)
            entries = enumerate_array_entries(path)
            if any(p.name != f"{encode_position(i)}.exr" for i, p in enumerate(entries)):
                raise ValueError("expected contiguous positional .exr members")
            return DepthFrames(tuple(DepthMap.read(p, spec=None, io=io) for p in entries))
        check_port(spec, False)
        import OpenEXR

        try:
            with OpenEXR.File(str(path), separate_channels=True, header_only=True) as exr:
                # Some OpenEXR 3.3 bindings report zero parts in header-only mode.
                header = read_exr_header(exr) if exr.parts else None
            with OpenEXR.File(str(path), separate_channels=True) as exr:
                frame, shape = header if header is not None else read_exr_header(exr)
                z = exr.channels()["Z"].pixels
        except RuntimeError as exc:
            raise ValueError(f"cannot read depth EXR {path}: {exc}") from exc
        if z.shape != shape:
            raise ValueError("depth EXR raster does not match its data window")
        result = DepthMap(z, frame)
        result.validate(spec=spec)
        return result


@dataclass(frozen=True)
class DepthMap(DepthCodec):
    z: np.ndarray
    frame: FrameMetadata | None = None

    def validate(self, *, spec):
        import numpy as np

        check_port(spec, False)
        if (
            not isinstance(self.z, np.ndarray)
            or self.z.dtype != np.float32
            or self.z.ndim != 2
            or not self.z.size
        ):
            raise ValueError("expected a nonempty HxW float32 depth raster")
        if self.frame is not None:
            if not isinstance(self.frame, FrameMetadata):
                raise ValueError("expected FrameMetadata")
            self.frame.validate()

    def write(self, path, *, spec, io):
        import numpy as np
        import OpenEXR

        self.validate(spec=spec)
        header = {"compression": OpenEXR.ZIP_COMPRESSION, "type": OpenEXR.scanlineimage}
        if self.frame is not None:
            header[FRAME_ATTRIBUTE] = json.dumps(asdict(self.frame), separators=(",", ":"))
        with OpenEXR.File(header, {"Z": np.ascontiguousarray(self.z)}) as exr:
            exr.write(str(path))


@dataclass(frozen=True)
class DepthFrames(DepthCodec):
    items: tuple[DepthMap, ...]

    def validate(self, *, spec):
        check_port(spec, True)
        for item in self.items:
            if not isinstance(item, DepthMap):
                raise ValueError("expected scalar depth_map array elements")
            item.validate(spec=None)

    def write(self, path, *, spec, io):
        self.validate(spec=spec)
        path.mkdir()
        for index, item in enumerate(self.items):
            item.write(path / f"{encode_position(index)}.exr", spec=None, io=io)
