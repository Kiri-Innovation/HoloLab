"""``frame-extraction@0.1.0`` — manifest shape + select-expr + ffmpeg integration.

Locks the STG parity contract: ``--max-width``, ``--start-frame``, ``--end-frame``
match ``SpacetimeGaussians/script/pre_no_prior.py`` and its
``pre_n3d.extractframes`` semantics — window is a half-open source-index
range, ``max_width`` only ever downscales, and combining with ``--skip``
strides from ``start_frame``.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

from hololab.manifest import load_manifest

_PACK_DIR = Path(__file__).resolve().parent.parent / "packs" / "frame-extraction@0.1.0"
_SCRIPT = _PACK_DIR / "extract_frames.py"


def _load_extract_frames_module() -> ModuleType:
    """Import ``extract_frames.py`` from the pack dir (dir name has ``@``,
    so a normal ``from packs...`` import isn't possible)."""
    spec = importlib.util.spec_from_file_location("extract_frames_pack", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Manifest shape — parity with STG pre_no_prior params.
# ---------------------------------------------------------------------------


def test_manifest_declares_stg_parity_params() -> None:
    m, _ = load_manifest(_PACK_DIR / "manifest.yaml")
    for p in ("skip", "max_frames", "max_width", "start_frame", "end_frame"):
        assert p in m.params, p
    # Defaults documented: 0 = "unset / native / no end" for all three new params.
    assert m.params["max_width"].default == 0
    assert m.params["start_frame"].default == 0
    assert m.params["end_frame"].default == 0
    # Pre-existing params keep their defaults (backward-compat).
    assert m.params["skip"].default == 1
    assert m.params["max_frames"].default == 50


def test_manifest_wires_new_flags_into_shell() -> None:
    m, _ = load_manifest(_PACK_DIR / "manifest.yaml")
    shell = m.exec.shell
    assert "--max-width {{ params.max_width }}" in shell
    assert "--start-frame {{ params.start_frame }}" in shell
    assert "--end-frame {{ params.end_frame }}" in shell


# ---------------------------------------------------------------------------
# ``_build_select_expr`` — pure logic covering every window x stride combo.
# ---------------------------------------------------------------------------


def test_select_expr_identity_returns_none() -> None:
    """skip=1 + no window = no filter — script stays on the fast path."""
    mod = _load_extract_frames_module()
    assert mod._build_select_expr(0, 0, 1) is None


def test_select_expr_skip_only_matches_legacy_form() -> None:
    """Skip without window mirrors the original ``not(mod(n,SKIP))`` used
    by earlier pack versions — backward compat for existing workflows."""
    mod = _load_extract_frames_module()
    # start=0 so mod(n-0, 2) == mod(n, 2) — semantically identical.
    assert mod._build_select_expr(0, 0, 2) == "not(mod(n-0,2))"


def test_select_expr_window_only_produces_bounds() -> None:
    """Window with skip=1 emits gte/lt bounds only (no stride term)."""
    mod = _load_extract_frames_module()
    assert mod._build_select_expr(3, 10, 1) == "gte(n,3)*lt(n,10)"


def test_select_expr_start_and_end_zero_end_means_no_upper_bound() -> None:
    """end=0 sentinel: no upper bound in the expression."""
    mod = _load_extract_frames_module()
    assert mod._build_select_expr(5, 0, 1) == "gte(n,5)"


def test_select_expr_stride_counted_from_start() -> None:
    """Stride starts from the window's first frame (start=3, skip=2 →
    keeps n=3,5,7… — NOT n=2,4,6…). Matches STG ``range(start, end, skip)``."""
    mod = _load_extract_frames_module()
    assert mod._build_select_expr(3, 10, 2) == "gte(n,3)*lt(n,10)*not(mod(n-3,2))"


# ---------------------------------------------------------------------------
# ``extract_frames`` argument validation.
# ---------------------------------------------------------------------------


def test_extract_frames_rejects_inverted_window(tmp_path: Path) -> None:
    """start >= end must fail fast — matches STG pre_no_prior.py:188-190
    (``if start_frame_num >= end_frame_num: quit()``)."""
    mod = _load_extract_frames_module()
    fake = tmp_path / "fake.mp4"
    fake.write_bytes(b"")  # existence-only guard; validation runs before decode.
    with pytest.raises(ValueError, match=r"start_frame.*<.*end_frame"):
        mod.extract_frames(fake, tmp_path / "out", start_frame=10, end_frame=5)


# ---------------------------------------------------------------------------
# Real-ffmpeg integration — synthetic testsrc video (~200 frames @ 320x240).
# Skips cleanly if ffmpeg isn't installed on the runner.
# ---------------------------------------------------------------------------


def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _make_synthetic_video(dst: Path, frames: int, w: int = 320, h: int = 240) -> None:
    """testsrc pattern with a moving frame counter — deterministic and small."""
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=duration={frames / 30}:size={w}x{h}:rate=30",
        "-pix_fmt",
        "yuv420p",
        str(dst),
    ]
    subprocess.run(cmd, check=True)


@pytest.fixture(scope="module")
def synthetic_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not _have_ffmpeg():
        pytest.skip("ffmpeg / ffprobe not installed")
    video = tmp_path_factory.mktemp("frames_video") / "src.mp4"
    _make_synthetic_video(video, frames=60)  # 2s @ 30fps
    return video


def test_max_width_downscales_only_when_wider(synthetic_video: Path, tmp_path: Path) -> None:
    """max_width=200 on a 320px-wide input → 200px output; input narrower
    than max_width would pass through unchanged (guarded by min(iw,W) in the
    ffmpeg filter). PNG-level width check via ffprobe on the first extracted
    frame is the ground-truth measurement."""
    mod = _load_extract_frames_module()
    out = tmp_path / "downscale"
    n = mod.extract_frames(synthetic_video, out, max_width=200)
    assert n > 0
    first = sorted(out.glob("frame_*.png"))[0]
    probed_w = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width",
            "-of",
            "csv=p=0",
            str(first),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert probed_w == "200", f"expected 200px wide, got {probed_w}"


def test_max_width_passthrough_when_input_narrower(synthetic_video: Path, tmp_path: Path) -> None:
    """max_width=9999 on a 320px input keeps native 320px — never upscales."""
    mod = _load_extract_frames_module()
    out = tmp_path / "passthrough"
    mod.extract_frames(synthetic_video, out, max_width=9999)
    first = sorted(out.glob("frame_*.png"))[0]
    probed_w = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width",
            "-of",
            "csv=p=0",
            str(first),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert probed_w == "320", f"max_width should never upscale; got {probed_w}"


def test_start_end_window_selects_exact_frame_count(synthetic_video: Path, tmp_path: Path) -> None:
    """``[start, end)`` = ``[10, 20)`` on a 60-frame source → exactly 10 frames.

    Verifies the half-open interval (STG parity) and that ffmpeg's select
    filter honors the source-index window. Overriding max_frames to a large
    value proves the window itself did the limiting."""
    mod = _load_extract_frames_module()
    out = tmp_path / "window"
    n = mod.extract_frames(synthetic_video, out, start_frame=10, end_frame=20, max_frames=999)
    assert n == 10, f"[10,20) must yield 10 frames, got {n}"


def test_stride_within_window_matches_stg_range(synthetic_video: Path, tmp_path: Path) -> None:
    """``[0, 20)`` with skip=4 → {0,4,8,12,16} = 5 frames (STG's
    ``range(start, end, skip)`` semantics)."""
    mod = _load_extract_frames_module()
    out = tmp_path / "stride"
    n = mod.extract_frames(
        synthetic_video,
        out,
        start_frame=0,
        end_frame=20,
        frame_skip=4,
        max_frames=999,
    )
    assert n == 5, f"range(0,20,4) has 5 frames, got {n}"
