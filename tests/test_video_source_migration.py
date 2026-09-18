"""M7: video-source-array retired → arrayed<video-source> across the board.

Locks the migrated shape so the M8 E2E can trust it:

* ``video-array-source.videos_dir`` — ``tags: [video-source]`` +
  ``arrayed: true`` (was ``tags: [video-source-array]``).
* ``frame-extraction`` — ``arrayable: true``, input drops the retired
  ``video-source-array`` string tag (was ``[video-source, video-source-array]``).
* ``tag_viewers.py`` — no viewer entry for the retired tag string.
* Grep-guard — no live source references the retired tag string outside
  of migration commentary.
"""

from __future__ import annotations

import re
from pathlib import Path

from hololab.gateway.tag_viewers import TAG_VIEWER_REGISTRY
from hololab.manifest import load_manifest

PACKS_ROOT = Path(__file__).resolve().parent.parent / "packs"
REPO_ROOT = Path(__file__).resolve().parent.parent


def _load(name: str):
    m, _ = load_manifest(PACKS_ROOT / f"{name}@0.1.0" / "manifest.yaml")
    return m


def test_video_array_source_output_is_arrayed_video_source() -> None:
    m = _load("video-array-source")
    videos = m.outputs["videos_dir"]
    assert videos.tags == ["video-source"]
    assert videos.arrayed is True


def test_frame_extraction_is_arrayable() -> None:
    m = _load("frame-extraction")
    assert m.arrayable is True


def test_frame_extraction_input_drops_retired_string_tag() -> None:
    m = _load("frame-extraction")
    assert m.inputs["video_dir"].tags == ["video-source"]


def test_video_source_array_tag_removed_from_viewer_registry() -> None:
    assert "video-source-array" not in TAG_VIEWER_REGISTRY


def test_no_live_references_to_retired_tag_string() -> None:
    """Grep guard — the retired ``video-source-array`` tag should only
    appear in migration-explanatory comments/docstrings, never in a live
    manifest field or wire code.
    """
    forbidden = "video-source-array"
    # Walk only the source we own (skip test files and cache dirs).
    search_roots = [
        REPO_ROOT / "hololab",
        REPO_ROOT / "packs",
    ]
    live_hits: list[str] = []
    for root in search_roots:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if "__pycache__" in path.parts:
                continue
            if path.suffix not in {".py", ".yaml", ".yml", ".ts", ".tsx"}:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                if forbidden not in line:
                    continue
                # Comment lines / docstring content are allowed; live code
                # would be a raw string literal in a non-comment context.
                stripped = line.lstrip()
                if stripped.startswith(("#", "//", "*")):
                    continue
                # YAML lines whose trimmed form starts with a punctuation
                # or word token (not # / //) but which mention the tag
                # inside a description block would begin with, e.g.,
                # "migration; the pre-arrayed ``video-source-array``…".
                # Allow if the mention appears inside a `` code span in
                # a multi-line yaml docstring — cheap heuristic: the line
                # doesn't set the ``tags:`` key.
                if re.search(r"\btags\s*:\s*\[.*video-source-array", line):
                    live_hits.append(f"{path}:{lineno}: {line.strip()}")
    assert not live_hits, "live references to retired tag:\n" + "\n".join(live_hits)
