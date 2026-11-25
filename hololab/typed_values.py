"""Worker-side values. Borrowed files must remain live until the invocation finishes."""

from __future__ import annotations

import io as streams
import shutil
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Generic, TypeVar

from PIL import Image as PILImage
from typing_extensions import Self

from hololab.array_positions import encode_position, enumerate_array_entries


@dataclass(frozen=True)
class RunContext:
    scratch: Path


class HType(ABC):
    @classmethod
    @abstractmethod
    def read(cls, path: Path, *, spec, io: RunContext) -> Self: ...

    @abstractmethod
    def validate(self, *, spec) -> None: ...

    @abstractmethod
    def write(self, path: Path, *, spec, io: RunContext) -> None: ...


@dataclass(frozen=True)
class Image(HType):
    encoded: Path | bytes

    def open_encoded(self) -> BinaryIO:
        if isinstance(self.encoded, bytes):
            return streams.BytesIO(self.encoded)
        return self.encoded.open("rb")

    @classmethod
    def read(cls, path, *, spec, io) -> Image:
        return cls(path)

    def format(self) -> str:
        with self.open_encoded() as stream, PILImage.open(stream) as image:
            if getattr(image, "n_frames", 1) != 1:
                raise ValueError("expected single-frame image")
            if min(image.size) <= 0:
                raise ValueError("invalid image dimensions")
            return image.format

    def validate(self, *, spec) -> None:
        self.format()

    def write(self, path, *, spec, io) -> None:
        with self.open_encoded() as source, path.open("xb") as target:
            shutil.copyfileobj(source, target)


T = TypeVar("T", bound=HType)


@dataclass(frozen=True)
class Array(HType, Sequence[T], Generic[T]):
    items: tuple[T, ...]

    def __getitem__(self, index):
        return self.items[index]

    def __len__(self):
        return len(self.items)

    @classmethod
    def read(cls, path, *, spec, io) -> Array[Image]:
        return cls(tuple(Image.read(p, spec=spec, io=io) for p in enumerate_array_entries(path)))

    def validate(self, *, spec) -> None:
        for index, item in enumerate(self):
            if not isinstance(item, Image):
                raise ValueError(f"expected image at position {index}")
            try:
                item.validate(spec=spec)
            except (ValueError, OSError) as exc:
                raise ValueError(f"invalid image at position {index}: {exc}") from exc

    def write(self, path, *, spec, io) -> None:
        path.mkdir()
        for index, image in enumerate(self):
            fmt = image.format().lower()
            image.write(path / f"{encode_position(index)}.{fmt}", spec=spec, io=io)


@dataclass(frozen=True)
class VideoSource(HType):
    video: Path
    metadata: tuple[Path, ...] = ()

    @classmethod
    def read(cls, path, *, spec, io) -> VideoSource:
        if path.is_file():
            return cls(path)
        # A video bundle contains one stream; sidecars have no positional meaning.
        entries = [p for p in path.iterdir() if not p.name.startswith(".")]
        metadata = tuple(p for p in entries if p.suffix == ".json" and p.is_file())
        videos = [p for p in entries if p not in metadata]
        if len(videos) != 1:
            raise ValueError("expected a single video with optional JSON sidecars")
        return cls(videos[0], metadata)

    def validate(self, *, spec) -> None:
        if not self.video.is_file() or self.video.suffix.lower() not in {
            ".mp4",
            ".mov",
            ".mkv",
            ".avi",
            ".webm",
            ".m4v",
        }:
            raise ValueError("invalid video-source")

    def write(self, path, *, spec, io) -> None:
        path.mkdir()
        for source in (self.video, *self.metadata):
            shutil.copyfile(source, path / source.name)


@dataclass(frozen=True)
class Colmap4D(HType):
    root: Path

    @classmethod
    def read(cls, path, *, spec, io) -> Colmap4D:
        return cls(path)

    def validate(self, *, spec) -> None:
        from colmap4d import validate_full
        from colmap4d.arrays import read_image_headers
        from colmap4d.validate import exit_code

        sparse = self.root / "sparse/0"
        issues = validate_full(sparse)
        if exit_code(issues) or not (self.root / "images").is_dir():
            raise ValueError(f"invalid colmap4d: {issues}")
        pixels = (self.root / "images").resolve()
        table = sparse / "images.bin"
        for image in read_image_headers(
            table if table.exists() else sparse / "images.txt"
        ).values():
            path = (pixels / image.name).resolve()
            if not path.is_relative_to(pixels) or not path.is_file():
                raise ValueError(f"invalid colmap4d image reference: {image.name}")

    def write(self, path, *, spec, io) -> None:
        shutil.copytree(self.root, path)


def codec(spec):
    tags = tuple(spec.tags)
    if (
        tags == ("image",)
        and spec.arrayed
        and spec.storage.value == "dir"
        and len(spec.dim_labels) <= 1
    ):
        return Array
    if not spec.arrayed:
        codecs = {("image",): Image, ("video-source",): VideoSource, ("colmap4d",): Colmap4D}
        storage = "file" if tags == ("image",) else "dir"
        if tags in codecs and spec.storage.value == storage:
            return codecs[tags]
    raise ValueError(f"unsupported typed port: {spec}")


def require(condition, message):
    if not condition:
        raise ValueError(message)
