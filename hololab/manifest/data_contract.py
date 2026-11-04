"""Concrete invocation contracts. No algorithm inference and no sampling.

Image leaves are individual decodable files. Collections are explicit directory
layers; dot-prefixed framework metadata is not an element. Symbolic links are
allowed (array utilities use them), but every referenced leaf is validated.
"""

import multiprocessing
import os
import threading
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from typing import Any

REMOVED_IMAGE_TAGS = frozenset({"image_sequence", "frame_sequence"})

# Leaves are checked in worker processes. Threads inside the node share one GIL
# with the event loop and every other shard's validation: 4 shards x 4 threads
# measured 6.7 4K-PNG frames/s, 8 processes 52 frames/s. The pool is created on
# first use, shared by the whole process and reused; workers start on demand.
IMAGE_DECODE_WORKERS = 8
# Leaves per task, amortising IPC (a 4K PNG decode is ~130 ms, a round trip <1 ms).
IMAGE_DECODE_CHUNK = 4
# In-process fallback used only if the worker pool breaks.
_FALLBACK_THREADS = 4

_pool: ProcessPoolExecutor | None = None
_pool_lock = threading.Lock()

# realpath -> identity of the file when this process last fully decoded it.
# An input leaf whose identity still matches was decoded before (when its
# producer's outputs were validated) and gets a structural verify() instead.
_VERIFIED_LIMIT = 2_000_000
_verified: dict[str, tuple[int, ...]] = {}
_verified_lock = threading.Lock()


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


def validate_path(
    path: Path,
    spec: Any,
    *,
    label: str,
    params: dict | None = None,
    trust_verified: bool = False,
) -> None:
    """Validate every image leaf, not just the first element or a summary count.

    Opaque tags retain their own bundle formats. `any` is explicitly unconstrained,
    rather than guessed to be an image. The path must still exist.

    With ``trust_verified`` a leaf this process already fully decoded, and whose
    file identity (device, inode, size, mtime, ctime) is unchanged since, is
    checked with ``verify()`` (structure and checksums) instead of a full decode.
    """
    variants = getattr(spec, "any_of", None) or []
    if variants:
        failures = []
        for variant in variants:
            try:
                validate_path(
                    path, variant, label=label, params=params, trust_verified=trust_verified
                )
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

    leaves: list[Path] = []

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
        leaves.append(current)

    try:
        # Structure first (cheap, ordered), then every leaf is checked.
        visit(path, depth)
        items = [(str(leaf), not (trust_verified and _still_verified(leaf))) for leaf in leaves]
        checks = _check_leaves(items)
        try:
            # Results arrive in traversal order, so the reported failure is the
            # first invalid leaf, as with sequential decoding.
            for leaf, (error, identity) in zip(leaves, checks, strict=True):
                if error is not None:
                    raise ContractError(f"{label}: invalid image file {leaf}: {error}")
                if identity is not None:
                    _remember_verified(leaf, identity)
        finally:
            checks.close()
    except OSError as exc:
        raise ContractError(f"{label}: cannot validate {path}: {exc}") from exc


def _identity(st: os.stat_result) -> tuple[int, ...]:
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _check_leaf(path: str, full: bool) -> tuple[str | None, tuple[int, ...] | None]:
    """Worker-side check: (error message or None, identity if fully decoded)."""
    from PIL import Image

    try:
        before = os.stat(path)
        with Image.open(path) as img:
            if getattr(img, "n_frames", 1) != 1:
                raise ValueError("multi-frame image is not a single image")
            if full:
                img.load()
            else:
                img.verify()
        after = _identity(os.stat(path))
    except Exception as exc:
        return str(exc), None
    # A file rewritten mid-decode is not remembered as verified.
    return None, after if full and after == _identity(before) else None


def _check_chunk(items: list[tuple[str, bool]]) -> list[tuple[str | None, tuple[int, ...] | None]]:
    results = []
    for path, full in items:
        results.append(_check_leaf(path, full))
        if results[-1][0] is not None:
            break
    return results


def _decode_pool() -> ProcessPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            # spawn: the node process runs threads, which fork() would not copy safely.
            _pool = ProcessPoolExecutor(
                IMAGE_DECODE_WORKERS, mp_context=multiprocessing.get_context("spawn")
            )
        return _pool


def shutdown_decode_pool() -> None:
    """Stop the worker processes; the next validation starts a new pool."""
    global _pool
    with _pool_lock:
        pool, _pool = _pool, None
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=True)


def _check_leaves(
    items: list[tuple[str, bool]],
) -> Iterator[tuple[str | None, tuple[int, ...] | None]]:
    """Yield one result per item, in order, stopping after the first failure."""
    global _pool
    if len(items) <= 1:
        yield from _check_chunk(items)
        return
    chunks = [items[i : i + IMAGE_DECODE_CHUNK] for i in range(0, len(items), IMAGE_DECODE_CHUNK)]
    pool = _decode_pool()
    futures = [pool.submit(_check_chunk, chunk) for chunk in chunks]
    done = 0
    try:
        for future in futures:
            try:
                results = future.result()
            except BrokenProcessPool:
                break
            done += 1
            yield from results
            if results[-1][0] is not None:
                return
        else:
            return
    finally:
        for future in futures:
            future.cancel()
    # A worker died (e.g. killed). Drop the pool and check the rest in-process.
    with _pool_lock:
        if _pool is pool:
            _pool = None
    pool.shutdown(wait=False, cancel_futures=True)
    rest = [item for chunk in chunks[done:] for item in chunk]
    with ThreadPoolExecutor(min(_FALLBACK_THREADS, len(rest))) as threads:
        for result in threads.map(lambda item: _check_leaf(*item), rest):
            yield result
            if result[0] is not None:
                return


def _still_verified(leaf: Path) -> bool:
    real = os.path.realpath(leaf)
    with _verified_lock:
        known = _verified.get(real)
    if known is None:
        return False
    try:
        return known == _identity(os.stat(real))
    except OSError:
        return False


def _remember_verified(leaf: Path, identity: tuple[int, ...]) -> None:
    real = os.path.realpath(leaf)
    with _verified_lock:
        if len(_verified) >= _VERIFIED_LIMIT:
            _verified.clear()
        _verified[real] = identity


def validate_ports(
    paths: dict[str, str], specs: dict, *, direction: str, params: dict | None = None
) -> None:
    for name, spec in specs.items():
        if name not in paths:
            if direction == "input" and not spec.required:
                continue
            raise ContractError(f"{direction}.{name}: required path missing")
        validate_path(
            Path(paths[name]),
            spec,
            label=f"{direction}.{name}",
            params=params,
            # Inputs were usually produced, and fully decoded, by an upstream job.
            trust_verified=direction == "input",
        )
