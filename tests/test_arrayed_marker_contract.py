"""Regression coverage for marker-safe arrayed element enumeration."""

from __future__ import annotations

import subprocess
from pathlib import Path

from hololab.manifest import load_manifest
from hololab.manifest.render import RenderContext, render_manifest

PACKS = Path(__file__).resolve().parents[1] / "packs"


def _run_shell(manifest_path: Path, *, arr: Path, output: Path, params: dict[str, int]) -> None:
    manifest, _ = load_manifest(manifest_path)
    shell = render_manifest(
        manifest,
        RenderContext(
            inputs={"arr": str(arr)}, outputs={"item": str(output), "n": str(output)}, params=params
        ),
    ).shell
    subprocess.run(["bash", "-c", shell], check=True, text=True, capture_output=True)


def _marker_array(root: Path) -> None:
    for name in ("g000001", "g000000"):
        (root / name).mkdir(parents=True)
    (root / ".hololab-done").touch()
    (root / ".hololab-metadata.json").write_text("{}")
    (root / "stray.txt").write_text("not an element")


def test_get_index_011_ignores_hololab_markers_and_counts_elements(tmp_path: Path) -> None:
    arr = tmp_path / "groups"
    arr.mkdir()
    _marker_array(arr)

    for index, expected in ((0, "g000000"), (1, "g000001")):
        item = tmp_path / f"item-{index}"
        _run_shell(
            PACKS / "get-index@0.1.1" / "manifest.yaml", arr=arr, output=item, params={"idx": index}
        )
        assert item.resolve() == arr / expected

    n = tmp_path / "n.txt"
    _run_shell(PACKS / "array-length@0.1.1" / "manifest.yaml", arr=arr, output=n, params={})
    assert n.read_text() == "2\n"


def test_arrayed_utility_shells_explicitly_filter_dot_directories() -> None:
    """Global convention guard for pack-side directory enumeration.

    Utility manifests that enumerate arrayed elements must spell the same
    ``-type d ! -name '.*'`` contract as the framework helper.
    """
    for name in ("get-index@0.1.1", "array-length@0.1.1"):
        text = (PACKS / name / "manifest.yaml").read_text()
        assert "-type d ! -name '.*'" in text
