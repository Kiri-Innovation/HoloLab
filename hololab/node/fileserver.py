"""Local read-only file server for previews and cross-node handle transfers.

The gateway reverse-proxies to this. Bind ``127.0.0.1`` by default; set
``advertised_url`` in node config only when a distinct interface exists.

Directory transfers use ``?archive=tar`` — the whole subtree is packed on the
fly into an uncompressed tar stream. The consumer (see
``hololab.node.runtime._fetch_remote_handle``) untars into its own workspace.
This is how the "storage form is a system detail, not a user concern"
guarantee is kept even across two machines.

Video thumbnails use ``GET /_thumb/{W}x{H}/{sub:path}`` — the node shells out
to ffmpeg to extract one frame at display size, caches the JPEG under
``{workspace_root}/.hololab-thumbs/``, and streams it back. This keeps the
gateway off the byte-hot-path for grid previews of directories that contain
many videos (a 20-video grid moves ~600 KiB of JPEG, not 2 GiB of video).

Video *proxy playback* uses ``GET /_preview/{W}x{H}/{sub:path}?fps=N`` — same
shape as ``/_thumb``, but the response is a small H.264-baseline MP4 that the
frontend can stream inside a ``<video>`` for grid tiles without decoding the
full-res source. That matters when a video-array-source drawer paints 21x
2.7K/30fps clips: 21 concurrent HW decoders is not something any browser
delivers on. Cached under ``{workspace_root}/.hololab-previews/``; see
``docs/video-preview-transcoding.md`` for the rationale, cost model and
fallback plan.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import shutil
import tarfile
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse

# Cap in-memory tarball size for the streaming path. Above this we fall back
# to a spooled temporary file so a huge directory doesn't OOM the node.
_INMEM_TAR_LIMIT_BYTES = 32 * 1024 * 1024  # 32 MiB

# Thumbnail size guardrails — the frontend renders at ~320x180 today. Cap
# generously to catch any future retina/hi-dpi bumps but refuse absurd
# values so a mistyped URL can't burn ffmpeg on a 4K decode.
_THUMB_MAX_DIM = 1024
_THUMB_CACHE_DIRNAME = ".hololab-thumbs"

# Preview MP4 (low-res proxy for grid tiles). Same dim guardrail as
# thumbs; also cap frame-rate so a mistyped URL can't ask for 240 fps
# transcoding of a 20-minute clip.
_PREVIEW_MAX_DIM = 1024
_PREVIEW_MAX_FPS = 60
_PREVIEW_CACHE_DIRNAME = ".hololab-previews"

# Cap concurrent ffmpeg transcodes across the whole process. Each
# transcode is a short-lived CPU burn (single-digit seconds for a 10s
# 2.7K clip → 320x180 with ultrafast preset); allowing all 21 tiles in a
# grid to spawn ffmpeg at once would just thrash. Four keeps the CPU
# usefully busy without pegging the box.
_PREVIEW_TRANSCODE_CONCURRENCY = 4
_preview_semaphore: asyncio.Semaphore | None = None


def _get_preview_semaphore() -> asyncio.Semaphore:
    """Lazily allocate the transcode semaphore so it binds to the
    running loop at first use rather than at import.
    """

    global _preview_semaphore
    if _preview_semaphore is None:
        _preview_semaphore = asyncio.Semaphore(_PREVIEW_TRANSCODE_CONCURRENCY)
    return _preview_semaphore


def create_fileserver_app(
    *,
    workspace_root: Path,
    legacy_workspace_roots: list[Path] | None = None,
) -> FastAPI:
    """Build a small FastAPI app that serves files under one or more roots.

    - ``GET /{sub}``                — returns the file at that path.
    - ``GET /{sub}?archive=tar``    — treats ``{sub}`` as a directory and
      returns an uncompressed tarball of its contents (member paths are
      relative to the directory, so extracting reconstructs the tree).

    Both variants enforce a per-root path-traversal guard: the resolved
    target must lie under ONE of the configured roots.

    When ``legacy_workspace_roots`` is supplied, the roots are tried in
    order (primary first, then legacy in list order) and the first root
    where the target exists wins. New writes always land under
    ``workspace_root`` — the extra roots exist so old artifacts stay
    resolvable through the preview proxy after a relocation.
    """

    app = FastAPI(title="HoloLab Node FileServer")
    primary = workspace_root.resolve()
    legacy = [p.resolve() for p in (legacy_workspace_roots or [])]
    all_roots: list[Path] = [primary, *legacy]

    def _resolve_under_any(sub: str) -> Path | None:
        """Return the first root where ``sub`` maps to an existing target.

        Traversal-safe: for each candidate root we compose ``root / sub``
        and normalise **lexically** (``os.path.normpath``) so ``..``
        collapses without chasing symlinks. The composed path must stay
        under the root; if it does, the file is served as-is and
        ``FileResponse`` follows any symlink at serve time.

        We deliberately do NOT ``.resolve()`` before the traversal check
        because packs may legitimately drop symlinks into their outputs
        that point outside the workspace (``single-video-source``
        symlinks the user's source video into ``videos_dir/input.<ext>``,
        for example). The trust model already assumes packs run with the
        same privileges as the file server, so a pack that writes a
        symlink can only expose files it could already read itself — no
        privilege escalation. ``..`` in the URL is still refused because
        it changes the *lexical* placement above the root.

        Absent under all roots → returns ``None`` (caller renders 404).
        """

        for root in all_roots:
            composed = Path(os.path.normpath(os.path.join(str(root), sub)))
            try:
                composed.relative_to(root)
            except ValueError:
                # Lexical ``..`` escape against this specific root — try next.
                continue
            # ``exists`` follows symlinks; ``is_symlink`` catches broken
            # links so the caller can still surface a 404 at read time
            # rather than us silently pretending the entry is missing.
            if composed.exists() or composed.is_symlink():
                return composed
        return None

    def _forbidden_across_all_roots(sub: str) -> bool:
        """True iff ``sub`` escapes EVERY configured root — 403 territory.

        Distinguishes "path exists under some root, target missing" (→404)
        from "path traversal against all roots" (→403). Empty roots list
        (shouldn't happen — primary is always present) counts as forbidden.
        """

        if not all_roots:
            return True
        for root in all_roots:
            composed = Path(os.path.normpath(os.path.join(str(root), sub)))
            try:
                composed.relative_to(root)
            except ValueError:
                continue
            return False  # at least one root accepts this sub-path
        return True

    # Thumbnail endpoint. Declared before the catch-all so FastAPI's route
    # matcher picks the specific ``/_thumb/…`` prefix first.
    #
    # URL: ``GET /_thumb/{W}x{H}/{sub:path}[?at=SECONDS]``
    # Emits: image/jpeg of the frame at ``?at`` (default 1.0s), resized to
    # fit within WxH (aspect-preserving letterbox via ffmpeg's ``scale``
    # filter). Cached under ``{primary_root}/{_THUMB_CACHE_DIRNAME}/`` keyed
    # by (source path, mtime, W, H, at) so mutating a video invalidates the
    # thumb.
    @app.get("/_thumb/{dims}/{sub:path}", response_model=None)
    async def _thumb(
        dims: str,
        sub: str,
        at: float = Query(default=1.0, ge=0.0, le=3600.0),
    ):
        try:
            width, height = _parse_thumb_dims(dims)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if _forbidden_across_all_roots(sub):
            raise HTTPException(status_code=403, detail="path outside workspace")
        target = _resolve_under_any(sub)
        if target is None:
            raise HTTPException(status_code=404, detail="video not found")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="not a file")

        thumb = await _thumb_response(target, width, height, at, cache_root=primary)
        return thumb

    # Video-preview endpoint. Same shape as ``/_thumb`` but returns a
    # small H.264-baseline MP4 that the frontend can stream inside a
    # ``<video>`` for grid tiles without decoding the 2.7K source. See
    # module docstring + ``docs/video-preview-transcoding.md`` for the
    # rationale.
    #
    # URL: ``GET /_preview/{W}x{H}/{sub:path}[?fps=N]``
    # Emits: video/mp4 with ``+faststart`` (moov box up front).
    # Cached under ``{primary_root}/{_PREVIEW_CACHE_DIRNAME}/`` keyed by
    # (source path, mtime, W, H, fps).
    @app.get("/_preview/{dims}/{sub:path}", response_model=None)
    async def _preview(
        dims: str,
        sub: str,
        fps: int = Query(default=15, ge=1, le=_PREVIEW_MAX_FPS),
    ):
        try:
            width, height = _parse_preview_dims(dims)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if _forbidden_across_all_roots(sub):
            raise HTTPException(status_code=403, detail="path outside workspace")
        target = _resolve_under_any(sub)
        if target is None:
            raise HTTPException(status_code=404, detail="video not found")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="not a file")

        return await _preview_response(target, width, height, fps, cache_root=primary)

    # Response type is intentionally omitted from the annotation: FastAPI
    # can't build a schema from a union of Response subclasses (each has its
    # own body model), and we don't need OpenAPI for this internal endpoint.
    @app.get("/{sub:path}", response_model=None)
    async def _get(sub: str, archive: str | None = Query(default=None)):
        if _forbidden_across_all_roots(sub):
            raise HTTPException(status_code=403, detail="path outside workspace")

        target = _resolve_under_any(sub)
        if target is None:
            raise HTTPException(status_code=404, detail="not found")

        if archive is not None:
            if archive != "tar":
                raise HTTPException(status_code=400, detail=f"unsupported archive={archive!r}")
            if not target.is_dir():
                raise HTTPException(status_code=404, detail="not a directory")
            return await _tar_response(target)

        if not target.is_file():
            raise HTTPException(status_code=404, detail="not a file")
        return FileResponse(target)

    return app


def _parse_thumb_dims(dims: str) -> tuple[int, int]:
    """Parse ``WxH`` — reject nonsense and outsized values.

    We refuse >``_THUMB_MAX_DIM`` on either axis so a mistyped URL can't
    stall ffmpeg on a 4K decode; the frontend never asks for anything
    larger than ~320x180.
    """

    if "x" not in dims:
        raise ValueError(f"invalid thumb dims {dims!r} (expected WxH)")
    w_s, h_s = dims.split("x", 1)
    try:
        w = int(w_s)
        h = int(h_s)
    except ValueError as exc:
        raise ValueError(f"invalid thumb dims {dims!r}: not integers") from exc
    if w <= 0 or h <= 0 or w > _THUMB_MAX_DIM or h > _THUMB_MAX_DIM:
        raise ValueError(
            f"invalid thumb dims {w}x{h}: must be 1..{_THUMB_MAX_DIM} on each axis"
        )
    return w, h


async def _thumb_response(
    video: Path, width: int, height: int, at: float, *, cache_root: Path
) -> FileResponse:
    """Return a JPEG frame of ``video`` at ``at`` seconds, resized to fit
    ``width``x``height`` (aspect preserved, letterbox padded).

    Caches results keyed by (path, mtime_ns, w, h, at). Cache misses shell
    out to ffmpeg with ``-frames:v 1 -update 1`` to write exactly one JPEG.
    Missing ffmpeg → 501 so the frontend can degrade gracefully to a
    browser-side ``<video>`` poster.
    """

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise HTTPException(status_code=501, detail="ffmpeg not available on this node")

    try:
        stat = video.stat()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"stat failed: {exc}") from exc

    key_material = f"{video.resolve()}|{stat.st_mtime_ns}|{width}x{height}|{at:.3f}".encode()
    digest = hashlib.sha256(key_material).hexdigest()[:32]
    cache_dir = cache_root / _THUMB_CACHE_DIRNAME
    cache_path = cache_dir / f"{digest}.jpg"

    if cache_path.is_file() and cache_path.stat().st_size > 0:
        return FileResponse(cache_path, media_type="image/jpeg")

    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_suffix(".tmp.jpg")

    # ``scale=w:h:force_original_aspect_ratio=decrease`` shrinks the frame
    # to fit inside the box without stretching; ``pad`` letterboxes to
    # exactly WxH so the frontend can rely on the returned dims. ``ss``
    # before ``-i`` is the fast seek; the encoder cost dominates.
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"
    )
    cmd = [
        ffmpeg,
        "-nostdin",
        "-loglevel", "error",
        "-ss", f"{at:.3f}",
        "-i", str(video),
        "-frames:v", "1",
        "-vf", vf,
        "-q:v", "3",
        "-y",
        str(tmp_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0 or not tmp_path.is_file() or tmp_path.stat().st_size == 0:
        tmp_path.unlink(missing_ok=True)
        detail = (err or b"").decode("utf-8", errors="replace")[:400] or "ffmpeg failed"
        raise HTTPException(status_code=500, detail=f"thumbnail extraction failed: {detail}")

    # Atomic rename inside the same directory — a concurrent request that
    # generated the same key would just clobber with an identical byte
    # sequence, so no locking needed.
    tmp_path.replace(cache_path)
    return FileResponse(cache_path, media_type="image/jpeg")


def _parse_preview_dims(dims: str) -> tuple[int, int]:
    """Same ``WxH`` parser as ``_parse_thumb_dims`` but with the
    preview-specific dimension cap. Kept separate so tightening one
    doesn't silently loosen the other.
    """

    if "x" not in dims:
        raise ValueError(f"invalid preview dims {dims!r} (expected WxH)")
    w_s, h_s = dims.split("x", 1)
    try:
        w = int(w_s)
        h = int(h_s)
    except ValueError as exc:
        raise ValueError(f"invalid preview dims {dims!r}: not integers") from exc
    if w <= 0 or h <= 0 or w > _PREVIEW_MAX_DIM or h > _PREVIEW_MAX_DIM:
        raise ValueError(
            f"invalid preview dims {w}x{h}: must be 1..{_PREVIEW_MAX_DIM} on each axis"
        )
    return w, h


async def _preview_response(
    video: Path, width: int, height: int, fps: int, *, cache_root: Path
) -> FileResponse:
    """Return a small H.264-baseline MP4 of ``video`` scaled to fit
    ``width``x``height`` at ``fps`` frames per second.

    Cached on disk under ``.hololab-previews/`` keyed by
    (path, mtime_ns, w, h, fps). Cache misses shell out to ffmpeg with
    ``ultrafast``/``baseline``/``+faststart`` — decoder work stays on
    the node (which typically has a real CPU) while the frontend
    streams the ~100 KiB output.

    Concurrent transcodes are capped process-wide (see
    ``_PREVIEW_TRANSCODE_CONCURRENCY``) so a 21-tile grid opening cold
    doesn't spawn 21 ffmpeg processes at once.
    """

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise HTTPException(status_code=501, detail="ffmpeg not available on this node")

    try:
        stat = video.stat()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"stat failed: {exc}") from exc

    key_material = f"{video.resolve()}|{stat.st_mtime_ns}|{width}x{height}|fps={fps}".encode()
    digest = hashlib.sha256(key_material).hexdigest()[:32]
    cache_dir = cache_root / _PREVIEW_CACHE_DIRNAME
    cache_path = cache_dir / f"{digest}.mp4"

    if cache_path.is_file() and cache_path.stat().st_size > 0:
        return FileResponse(cache_path, media_type="video/mp4")

    cache_dir.mkdir(parents=True, exist_ok=True)
    # Unique tmp name so two concurrent transcodes on the same key don't
    # trample each other's output mid-encode. Rename below is atomic on
    # the same filesystem.
    tmp_path = cache_path.with_suffix(f".tmp.{os.getpid()}.mp4")

    # Same letterbox scale/pad as ``/_thumb`` so grid tiles line up
    # frame-to-frame with their poster thumbnails. Framerate is set
    # via the ``fps=`` FILTER (not ``-r``) — ``fps=N`` resamples by
    # dropping or duplicating frames while preserving the source's
    # timeline, which keeps output duration == source duration to
    # the frame. Using ``-r`` instead let ffmpeg pick output
    # timestamps, which on a 30 fps x 10 s source produced 152
    # frames / 10.133 s instead of 150 / 10.000 s — a 133 ms drift
    # that the frontend's shared-bar scrubber then inherits.
    #
    # ``ultrafast`` + ``baseline`` keep decoders happy on every
    # browser we care about (Chromium, Firefox, Safari all HW-decode
    # H.264 baseline); we trade a small filesize increase for ~5x
    # faster encode.
    #
    # ``-an`` strips audio — the frontend plays grid tiles muted and
    # we don't want to spend bytes on soundtrack we throw away.
    # ``+faststart`` moves the moov box to the head of the file so the
    # ``<video preload="metadata">`` fetch on drawer-open returns
    # something playable in the first few KiB, not after the whole
    # download.
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"fps={fps}"
    )
    cmd = [
        ffmpeg,
        "-nostdin",
        "-loglevel", "error",
        "-i", str(video),
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-profile:v", "baseline",
        "-pix_fmt", "yuv420p",
        "-crf", "28",
        "-an",
        "-movflags", "+faststart",
        "-y",
        str(tmp_path),
    ]

    sem = _get_preview_semaphore()
    async with sem:
        # Re-check the cache under the semaphore in case a peer request
        # already wrote it while we were waiting for our slot — saves a
        # redundant ffmpeg spawn under contention.
        if cache_path.is_file() and cache_path.stat().st_size > 0:
            tmp_path.unlink(missing_ok=True)
            return FileResponse(cache_path, media_type="video/mp4")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()

    if proc.returncode != 0 or not tmp_path.is_file() or tmp_path.stat().st_size == 0:
        tmp_path.unlink(missing_ok=True)
        detail = (err or b"").decode("utf-8", errors="replace")[:400] or "ffmpeg failed"
        raise HTTPException(status_code=500, detail=f"preview transcode failed: {detail}")

    tmp_path.replace(cache_path)
    return FileResponse(cache_path, media_type="video/mp4")


async def _tar_response(dir_path: Path) -> StreamingResponse:
    """Build (in the background) and stream a tarball of ``dir_path``.

    We assemble the archive off the event loop with ``asyncio.to_thread`` so
    the loop stays responsive to other requests / heartbeats. For small dirs
    (well under 32 MiB) it stays entirely in memory; larger dirs spool to a
    temp file which is streamed back and cleaned up on close.
    """

    def _make_archive() -> tuple[io.IOBase, int]:
        # Use a real spooled temp file rather than BytesIO so we don't hold
        # the whole tar in RAM for larger directory trees. The spool
        # threshold matches the module-level constant.
        import tempfile

        spooled: io.IOBase = tempfile.SpooledTemporaryFile(max_size=_INMEM_TAR_LIMIT_BYTES)  # noqa: SIM115 — we manage close in the streamer
        with tarfile.open(fileobj=spooled, mode="w") as tar:
            tar.add(dir_path, arcname=".", recursive=True)
        size = spooled.tell()
        spooled.seek(0)
        return spooled, size

    stream, size = await asyncio.to_thread(_make_archive)

    async def _iter():
        try:
            while True:
                chunk = await asyncio.to_thread(stream.read, 64 * 1024)
                if not chunk:
                    return
                yield chunk
        finally:
            stream.close()

    headers = {"Content-Length": str(size)}
    return StreamingResponse(
        _iter(),
        media_type="application/x-tar",
        headers=headers,
    )


async def serve_fileserver(app: FastAPI, *, host: str, port: int) -> None:
    """Run the file server inside the current asyncio loop.

    Thin wrapper around :class:`ServeHandle`. Prefer ``ServeHandle`` for
    code that needs to swap file servers live (e.g. workspace_root
    change) — it exposes ``stop()`` which releases the port cleanly
    before you rebind on the same host:port.
    """

    handle = ServeHandle(app, host=host, port=port)
    await handle.serve()


class ServeHandle:
    """Owning wrapper around a uvicorn :class:`~uvicorn.Server` instance.

    The naive ``await server.serve()`` inside a task looks fine until you
    ``task.cancel()`` it and immediately try to bind on the same port —
    uvicorn doesn't get a chance to release the socket during raw
    cancellation, so the next bind fails with ``EADDRINUSE``. Setting
    ``should_exit = True`` and awaiting the task lets uvicorn tear down
    its transports properly.
    """

    def __init__(self, app: FastAPI, *, host: str, port: int) -> None:
        self._config = uvicorn.Config(app, host=host, port=port, log_level="warning")
        # Give the graceful-shutdown path a chance to release the socket
        # before the next rebind. Uvicorn defaults to 5s; we keep that.
        self._config.timeout_graceful_shutdown = 5
        self._server = uvicorn.Server(self._config)

    async def serve(self) -> None:
        await self._server.serve()

    async def stop(self) -> None:
        """Ask uvicorn to shut down and wait for the socket to close."""

        self._server.should_exit = True
        # Give the reactor a beat to drain and release the port. The
        # actual join is done by the caller who owns the task.
