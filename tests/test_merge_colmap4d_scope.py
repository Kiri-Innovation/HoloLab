"""The published merge pack contains exactly its runtime dependency closure."""

import shutil
import tarfile
from pathlib import Path

from hololab.content_scope import Baselines, capture
from hololab.pack_publish import publish

PACK = Path(__file__).resolve().parents[1] / "community_packs/merge-colmap4d@0.5.0"


def test_scope_and_publication(tmp_path):
    pack = tmp_path / "source"
    shutil.copytree(PACK, pack)
    manifest = pack / "manifest.yaml"
    baselines = Baselines(tmp_path / "baselines")
    original = capture(manifest, baselines)["sha256"]
    (pack / "README.md").write_text("Changed documentation")
    (pack / "test_probe.py").write_text('raise RuntimeError("must not ship")')
    assert capture(manifest, baselines)["sha256"] == original
    registry = tmp_path / "registry"
    registry.mkdir()
    release = publish(manifest, registry.as_uri(), "community")
    assert release.code_sha256 == original
    with tarfile.open(registry / release.archive) as archive:
        assert set(archive.getnames()) == {
            ".packignore",
            "manifest.yaml",
            "merge_colmap4d.py",
            "strict_legacy.py",
        }
        archive.extractall(tmp_path / "installed", filter="data")
    assert capture(tmp_path / "installed/manifest.yaml", baselines)["sha256"] == original
    with (pack / "strict_legacy.py").open("a") as stream:
        stream.write("\n# runtime change\n")
    assert capture(manifest, baselines)["sha256"] != original
