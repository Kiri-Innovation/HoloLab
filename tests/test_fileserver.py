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
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=64x48:d=1:r=10",
        "-pix_fmt",
        "yuv420p",
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


def _make_tiny_jpeg(path: Path) -> None:
    """Encode a 4x4 solid-colour JPEG via ffmpeg — cheap seed for the
    still-image thumb path.

    Motivation: JPEG demuxers report duration 0, so the earlier
    ``ffmpeg -ss 0 -i <jpg>`` path silently emitted zero bytes (rc=0,
    tmp file missing → 500 for the caller). The still-image branch of
    ``_thumb_response`` drops ``-ss`` for these inputs, and this test
    is the regression pin.
    """

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg not available")
    cmd = [
        ffmpeg,
        "-nostdin",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=red:s=4x4:d=1:r=1",
        "-frames:v",
        "1",
        "-q:v",
        "3",
        "-y",
        str(path),
    ]
    subprocess.run(cmd, check=True)


def test_thumb_still_image_jpeg(tmp_path: Path) -> None:
    """Regression: JPEG inputs used to 500 because the video-style
    ``-ss 0`` seek encoded zero frames. The still-image branch skips
    ``-ss`` and returns a real thumbnail JPEG."""

    src = tmp_path / "frame.jpg"
    _make_tiny_jpeg(src)
    app = create_fileserver_app(workspace_root=tmp_path)
    with TestClient(app) as client:
        r = client.get("/_thumb/64x36/frame.jpg", params={"at": 0.0})
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "image/jpeg"
        assert r.content[:2] == b"\xff\xd8"
        cache_dir = tmp_path / ".hololab-thumbs"
        assert cache_dir.is_dir()
        assert len(list(cache_dir.glob("*.jpg"))) == 1


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
            ffmpeg,
            "-nostdin",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=128x72:d=2:r=30",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(source),
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
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-show_entries",
                "stream=nb_frames,avg_frame_rate",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(out_path),
            ],
            check=True,
            capture_output=True,
            text=True,
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
    assert nb_frames == 30, f"frame count off: {nb_frames} (expected 30 @ 15 fps x 2 s)"


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


# ---------------------------------------------------------------------------
# Caching headers + conditional-request short-circuits
#
# The "F5 spam" pathology: 200 same-URL thumb requests on refresh used to
# spawn ~30 ffmpeg processes and starve /api. The fix has two legs:
#   * per-response ``Cache-Control`` + ``ETag`` + ``Last-Modified`` so the
#     browser reuses its own cache on same-URL refreshes,
#   * 304 short-circuit when the browser revalidates so a stale cache
#     round-trips without touching ffmpeg or the disk cache.
# These tests pin both.
# ---------------------------------------------------------------------------


def test_thumb_response_has_cache_headers(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    _make_tiny_mp4(video)
    app = create_fileserver_app(workspace_root=tmp_path)
    with TestClient(app) as client:
        r = client.get("/_thumb/64x36/clip.mp4", params={"at": 0.0})
        assert r.status_code == 200, r.text
        cc = r.headers.get("cache-control", "")
        assert "public" in cc and "max-age=" in cc, cc
        assert r.headers.get("etag", "").startswith('"'), r.headers.get("etag")
        assert r.headers.get("last-modified"), "last-modified must be present"


def test_thumb_if_none_match_returns_304(tmp_path: Path) -> None:
    """Same-URL refresh once the client already has the ETag: server
    must 304 without invoking ffmpeg or reading the cache file."""

    video = tmp_path / "clip.mp4"
    _make_tiny_mp4(video)
    app = create_fileserver_app(workspace_root=tmp_path)
    with TestClient(app) as client:
        r1 = client.get("/_thumb/64x36/clip.mp4", params={"at": 0.0})
        assert r1.status_code == 200
        etag = r1.headers["etag"]
        r2 = client.get(
            "/_thumb/64x36/clip.mp4",
            params={"at": 0.0},
            headers={"If-None-Match": etag},
        )
        assert r2.status_code == 304
        # 304 must carry no body.
        assert r2.content == b""
        # And it must echo the ETag so the browser can keep the entry.
        assert r2.headers.get("etag") == etag


def test_thumb_if_modified_since_returns_304(tmp_path: Path) -> None:
    """Second refresh with ``If-Modified-Since`` set to the response's
    ``Last-Modified``: 304, no body."""

    video = tmp_path / "clip.mp4"
    _make_tiny_mp4(video)
    app = create_fileserver_app(workspace_root=tmp_path)
    with TestClient(app) as client:
        r1 = client.get("/_thumb/64x36/clip.mp4", params={"at": 0.0})
        assert r1.status_code == 200
        last_mod = r1.headers["last-modified"]
        r2 = client.get(
            "/_thumb/64x36/clip.mp4",
            params={"at": 0.0},
            headers={"If-Modified-Since": last_mod},
        )
        assert r2.status_code == 304
        assert r2.content == b""


def test_thumb_stale_etag_does_not_hit_304(tmp_path: Path) -> None:
    """Old ETag from a previous mtime must NOT match — the server must
    serve the fresh JPEG, not a stale 304."""

    video = tmp_path / "clip.mp4"
    _make_tiny_mp4(video)
    app = create_fileserver_app(workspace_root=tmp_path)
    with TestClient(app) as client:
        r = client.get(
            "/_thumb/64x36/clip.mp4",
            params={"at": 0.0},
            headers={"If-None-Match": '"deadbeef-not-the-real-tag"'},
        )
        assert r.status_code == 200, r.text
        assert r.content[:2] == b"\xff\xd8"


def test_preview_response_has_cache_headers(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    _make_tiny_mp4(video)
    app = create_fileserver_app(workspace_root=tmp_path)
    with TestClient(app) as client:
        r = client.get("/_preview/64x36/clip.mp4", params={"fps": 10})
        assert r.status_code == 200, r.text
        cc = r.headers.get("cache-control", "")
        assert "public" in cc and "max-age=" in cc, cc
        assert r.headers.get("etag", "").startswith('"'), r.headers.get("etag")


def test_preview_if_none_match_returns_304(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    _make_tiny_mp4(video)
    app = create_fileserver_app(workspace_root=tmp_path)
    with TestClient(app) as client:
        r1 = client.get("/_preview/64x36/clip.mp4", params={"fps": 10})
        assert r1.status_code == 200
        etag = r1.headers["etag"]
        r2 = client.get(
            "/_preview/64x36/clip.mp4",
            params={"fps": 10},
            headers={"If-None-Match": etag},
        )
        assert r2.status_code == 304
        assert r2.content == b""


def test_thumb_extract_concurrency_capped(tmp_path: Path) -> None:
    """Cache misses must serialise behind
    :data:`~hololab.node.fileserver._THUMB_EXTRACT_CONCURRENCY` — the
    guard against the "F5 spawns 30 ffmpegs" pathology. We patch
    ``asyncio.create_subprocess_exec`` to observe peak concurrency and
    hit the endpoint from a small in-memory async client.
    """

    import asyncio
    import contextlib

    from httpx import ASGITransport, AsyncClient

    from hololab.node import fileserver as fs

    video = tmp_path / "clip.mp4"
    _make_tiny_mp4(video)
    # Force a fresh semaphore bound to this test's event loop.
    fs._thumb_semaphore = None

    active = 0
    peak = 0

    real_exec = asyncio.create_subprocess_exec

    async def spying_exec(*args: object, **kwargs: object):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            # Hold the slot briefly so concurrent callers pile up on the
            # semaphore — otherwise the first spawn returns before the
            # rest arrive and we'd observe peak=1 even with no limit.
            await asyncio.sleep(0.05)
            return await real_exec(*args, **kwargs)  # type: ignore[misc]
        finally:
            active -= 1

    app = create_fileserver_app(workspace_root=tmp_path)

    async def run() -> None:
        with contextlib.suppress(Exception):
            pass
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 16 distinct sub-second offsets → 16 distinct cache keys → 16
            # cache misses that all have to pass through ffmpeg. Keep the
            # offsets inside the source's 1 s duration.
            tasks = [
                client.get(
                    "/_thumb/64x36/clip.mp4",
                    params={"at": round(0.05 + i * 0.05, 3)},
                )
                for i in range(16)
            ]
            results = await asyncio.gather(*tasks)
            for r in results:
                assert r.status_code == 200, r.text

    original = asyncio.create_subprocess_exec
    asyncio.create_subprocess_exec = spying_exec  # type: ignore[assignment]
    try:
        asyncio.run(run())
    finally:
        asyncio.create_subprocess_exec = original  # type: ignore[assignment]

    assert peak <= fs._THUMB_EXTRACT_CONCURRENCY, (
        f"thumb ffmpeg peak {peak} exceeded cap {fs._THUMB_EXTRACT_CONCURRENCY}"
    )
