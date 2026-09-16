"""File server: single-file GET and ``?archive=tar`` directory transfer.

The tarball path is what makes "storage form is a system detail" hold across
machines — a downstream node materialising a directory-shaped handle asks for
the tarball, extracts it, and hands the pack a real local path.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hololab.node.fileserver import create_fileserver_app


def test_get_file(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("world")
    app = create_fileserver_app(workspace_root=tmp_path)
    with TestClient(app) as client:
        r = client.get("/hello.txt")
        assert r.status_code == 200
        assert r.content == b"world"


def test_tar_archive_of_directory(tmp_path: Path) -> None:
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "a.txt").write_text("alpha")
    (tmp_path / "d" / "sub").mkdir()
    (tmp_path / "d" / "sub" / "b.txt").write_text("bravo")

    app = create_fileserver_app(workspace_root=tmp_path)
    with TestClient(app) as client:
        r = client.get("/d", params={"archive": "tar"})
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/x-tar"

        # Verify the tarball round-trips the directory tree.
        with tarfile.open(fileobj=io.BytesIO(r.content), mode="r") as tar:
            names = sorted(m.name for m in tar.getmembers() if m.isfile())
        assert names == ["./a.txt", "./sub/b.txt"]


def test_tar_rejects_non_directory(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("world")
    app = create_fileserver_app(workspace_root=tmp_path)
    with TestClient(app) as client:
        r = client.get("/hello.txt", params={"archive": "tar"})
        assert r.status_code == 404


def test_path_traversal_blocked(tmp_path: Path) -> None:
    (tmp_path / "inside.txt").write_text("safe")
    app = create_fileserver_app(workspace_root=tmp_path)
    with TestClient(app) as client:
        # ``..`` climbs above workspace root — must be refused.
        r = client.get("/../etc/hosts")
        assert r.status_code in (403, 404)  # depends on how starlette normalizes


def test_symlink_pointing_outside_workspace_is_served(tmp_path: Path) -> None:
    """Symlinks written by packs (e.g. single-video-source pointing at the
    user's source video) must remain accessible via the preview proxy.

    The traversal check is lexical only — symlink targets outside the
    workspace are followed at serve time because packs and the file
    server share privileges."""

    target_dir = tmp_path / "outside"
    target_dir.mkdir()
    real = target_dir / "video.mp4"
    real.write_bytes(b"binary-video-bytes")

    root = tmp_path / "workspace"
    (root / "job").mkdir(parents=True)
    link = root / "job" / "input.mp4"
    link.symlink_to(real)

    app = create_fileserver_app(workspace_root=root)
    with TestClient(app) as client:
        r = client.get("/job/input.mp4")
        assert r.status_code == 200
        assert r.content == b"binary-video-bytes"


# ---------------------------------------------------------------------------
# Thumbnail endpoint
# ---------------------------------------------------------------------------


def _make_tiny_mp4(path: Path) -> None:
    """Encode a 1s solid-colour clip via ffmpeg — cheap seed for the tests.

    Skips the caller if ffmpeg isn't installed; the thumb endpoint itself
    already 501s in that case and there's nothing to exercise.
    """

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg not available")
    cmd = [
        ffmpeg,
        "-nostdin",
        "-loglevel", "error",
        "-f", "lavfi",
        "-i", "color=c=blue:s=64x48:d=1:r=10",
        "-pix_fmt", "yuv420p",
        "-y",
        str(path),
    ]
    subprocess.run(cmd, check=True)


def test_thumb_extracts_jpeg(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    _make_tiny_mp4(video)
    app = create_fileserver_app(workspace_root=tmp_path)
    with TestClient(app) as client:
        r = client.get("/_thumb/64x36/clip.mp4", params={"at": 0.1})
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "image/jpeg"
        # JPEG SOI marker.
        assert r.content[:2] == b"\xff\xd8"
        # Cache directory is populated (subsequent hits should be instant).
        cache_dir = tmp_path / ".hololab-thumbs"
        assert cache_dir.is_dir()
        jpgs = list(cache_dir.glob("*.jpg"))
        assert len(jpgs) == 1


def test_thumb_cached_on_second_hit(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    _make_tiny_mp4(video)
    app = create_fileserver_app(workspace_root=tmp_path)
    with TestClient(app) as client:
        # ``at=0`` — the 1s test clip has no frame past its own duration, so
        # ask for the first frame to be sure ffmpeg finds something.
        r1 = client.get("/_thumb/64x36/clip.mp4", params={"at": 0.0})
        assert r1.status_code == 200, r1.text
        cache_dir = tmp_path / ".hololab-thumbs"
        jpg = next(cache_dir.glob("*.jpg"))
        mtime = jpg.stat().st_mtime_ns
        # Second request must hit the cache — mtime should not change.
        r2 = client.get("/_thumb/64x36/clip.mp4", params={"at": 0.0})
        assert r2.status_code == 200
        assert jpg.stat().st_mtime_ns == mtime


def test_thumb_bad_dims_rejected(tmp_path: Path) -> None:
    (tmp_path / "clip.mp4").write_bytes(b"stub")
    app = create_fileserver_app(workspace_root=tmp_path)
    with TestClient(app) as client:
        # Non-integer.
        assert client.get("/_thumb/abc/clip.mp4").status_code == 400
        # Missing separator.
        assert client.get("/_thumb/320/clip.mp4").status_code == 400
        # Absurdly oversized — refused before shelling out to ffmpeg.
        assert client.get("/_thumb/9999x9999/clip.mp4").status_code == 400


def test_thumb_missing_file_404(tmp_path: Path) -> None:
    app = create_fileserver_app(workspace_root=tmp_path)
    with TestClient(app) as client:
        r = client.get("/_thumb/64x36/does-not-exist.mp4")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Preview (low-res proxy MP4) endpoint
# ---------------------------------------------------------------------------


def test_preview_transcodes_mp4(tmp_path: Path) -> None:
    """Cold hit: preview endpoint returns a playable MP4 keyed by
    (path, mtime, W, H, fps) under ``.hololab-previews/``.
    """

    video = tmp_path / "clip.mp4"
    _make_tiny_mp4(video)
    app = create_fileserver_app(workspace_root=tmp_path)
    with TestClient(app) as client:
        r = client.get("/_preview/64x36/clip.mp4", params={"fps": 10})
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "video/mp4"
        # ISO base media file signature: ``ftyp`` box near the start.
        assert b"ftyp" in r.content[:64]
        cache_dir = tmp_path / ".hololab-previews"
        assert cache_dir.is_dir()
        mp4s = list(cache_dir.glob("*.mp4"))
        assert len(mp4s) == 1


def test_preview_preserves_source_duration(tmp_path: Path) -> None:
    """Regression: the fps=N *filter* must be used (not ``-r N``) so
    output duration matches the source to the frame. ``-r`` used to
    stretch a 10 s / 30 fps source to 10.133 s / 152 frames instead
    of 10 s / 150 frames when downsampling to 15 fps, which showed
    up in the shared playback bar as an out-of-sync end time.
    """

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg not available")
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        pytest.skip("ffprobe not available")

    # 2 s source at 30 fps → 60 frames.
    source = tmp_path / "src.mp4"
    subprocess.run(
        [
            ffmpeg, "-nostdin", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=red:s=128x72:d=2:r=30",
            "-pix_fmt", "yuv420p", "-y", str(source),
        ],
        check=True,
    )

    app = create_fileserver_app(workspace_root=tmp_path)
    with TestClient(app) as client:
        # Target 15 fps → 30 frames → exactly 2.000 s.
        r = client.get("/_preview/64x36/src.mp4", params={"fps": 15})
        assert r.status_code == 200, r.text
        out_path = tmp_path / "out.mp4"
        out_path.write_bytes(r.content)

        probe = subprocess.run(
            [
                ffprobe, "-v", "error",
                "-show_entries", "format=duration",
                "-show_entries", "stream=nb_frames,avg_frame_rate",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(out_path),
            ],
            check=True, capture_output=True, text=True,
        )
        lines = [ln for ln in probe.stdout.strip().splitlines() if ln]
        # Order (ffprobe emits stream entries then format):
        #   avg_frame_rate, nb_frames, duration
        rate, nb_frames_str, duration_str = lines
        nb_frames = int(nb_frames_str)
        duration = float(duration_str)

    # Source is exactly 2.000 s; the preview should match to within
    # a frame's worth of jitter.
    assert abs(duration - 2.000) < 0.05, (
        f"preview duration drifted: {duration:.4f} s (expected ~2.000 s); "
        f"rate={rate}, nb_frames={nb_frames}"
    )
    # At fps=15 for a 2 s clip we expect exactly 30 frames.
    assert nb_frames == 30, (
        f"frame count off: {nb_frames} (expected 30 @ 15 fps x 2 s)"
    )


def test_preview_cached_on_second_hit(tmp_path: Path) -> None:
    """Warm hit: second request must not re-transcode — the cached
    file's mtime is the tell.
    """

    video = tmp_path / "clip.mp4"
    _make_tiny_mp4(video)
    app = create_fileserver_app(workspace_root=tmp_path)
    with TestClient(app) as client:
        r1 = client.get("/_preview/64x36/clip.mp4", params={"fps": 10})
        assert r1.status_code == 200, r1.text
        cache_dir = tmp_path / ".hololab-previews"
        mp4 = next(cache_dir.glob("*.mp4"))
        mtime = mp4.stat().st_mtime_ns
        r2 = client.get("/_preview/64x36/clip.mp4", params={"fps": 10})
        assert r2.status_code == 200
        assert mp4.stat().st_mtime_ns == mtime


def test_preview_bad_dims_rejected(tmp_path: Path) -> None:
    (tmp_path / "clip.mp4").write_bytes(b"stub")
    app = create_fileserver_app(workspace_root=tmp_path)
    with TestClient(app) as client:
        assert client.get("/_preview/abc/clip.mp4").status_code == 400
        assert client.get("/_preview/320/clip.mp4").status_code == 400
        # Absurd dims — refused before shelling out to ffmpeg.
        assert client.get("/_preview/9999x9999/clip.mp4").status_code == 400


def test_preview_bad_fps_rejected(tmp_path: Path) -> None:
    (tmp_path / "clip.mp4").write_bytes(b"stub")
    app = create_fileserver_app(workspace_root=tmp_path)
    with TestClient(app) as client:
        # 0 fps → FastAPI validation kicks in (ge=1).
        assert client.get("/_preview/64x36/clip.mp4", params={"fps": 0}).status_code == 422
        # Absurdly high fps — refused before ffmpeg.
        assert client.get("/_preview/64x36/clip.mp4", params={"fps": 999}).status_code == 422


def test_preview_missing_file_404(tmp_path: Path) -> None:
    app = create_fileserver_app(workspace_root=tmp_path)
    with TestClient(app) as client:
        r = client.get("/_preview/64x36/does-not-exist.mp4")
        assert r.status_code == 404


def test_preview_cache_key_includes_dims_and_fps(tmp_path: Path) -> None:
    """Two different (W, H, fps) tuples must produce two cache entries
    for the same source video — otherwise a caller can only ever see
    the first tuple that landed.
    """

    video = tmp_path / "clip.mp4"
    _make_tiny_mp4(video)
    app = create_fileserver_app(workspace_root=tmp_path)
    with TestClient(app) as client:
        assert client.get("/_preview/64x36/clip.mp4", params={"fps": 10}).status_code == 200
        assert client.get("/_preview/128x72/clip.mp4", params={"fps": 10}).status_code == 200
        assert client.get("/_preview/64x36/clip.mp4", params={"fps": 15}).status_code == 200
        cache_dir = tmp_path / ".hololab-previews"
        assert len(list(cache_dir.glob("*.mp4"))) == 3
