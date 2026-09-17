"""Multi-pack-source scanner + NodeConfig backward-compat tests.

Covers:
    * ``scan_multi_packs`` iterates every root and reports ``source_dir``.
    * Conflict rule is first-wins with a warning.
    * ``NodeConfig`` loads a legacy ``packs_dir: /path`` config into a
      single-entry ``pack_dirs``.
    * ``NodeConfig`` with both ``packs_dir`` and ``pack_dirs`` merges
      the scalar onto the front of the list (first-wins semantics).
    * ``NodeConfig(pack_dirs=[])`` falls back to the default so an
      operator can't accidentally leave the node with zero pack roots.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import yaml

from hololab.node.config import NodeConfig, load_node_config
from hololab.node.packs import scan_multi_packs, scan_packs


def _write_manifest(pack_dir: Path, name: str, version: str) -> None:
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "manifest.yaml").write_text(
        textwrap.dedent(
            f"""\
            apiVersion: hololab.dev/v1
            kind: Algorithm
            name: {name}
            version: {version}
            description: test pack
            outputs:
              out:
                tags: [text]
            runtime:
              env: kiri
            exec:
              shell: "true"
            """
        )
    )


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


def test_scan_multi_packs_reports_source_dir(tmp_path: Path) -> None:
    root_a = tmp_path / "vendored"
    root_b = tmp_path / "user"
    _write_manifest(root_a / "alpha@0.1.0", "alpha", "0.1.0")
    _write_manifest(root_b / "beta@0.2.0", "beta", "0.2.0")

    packs = scan_multi_packs([root_a, root_b])
    by_name = {p.manifest.name: p for p in packs}

    assert set(by_name) == {"alpha", "beta"}
    assert by_name["alpha"].source_dir == root_a
    assert by_name["beta"].source_dir == root_b


def test_scan_multi_packs_first_wins(tmp_path: Path) -> None:
    """Same ``name@version`` in two roots → the earlier root keeps its
    pack, the later root's copy is silently dropped from the output.
    """

    root_a = tmp_path / "vendored"
    root_b = tmp_path / "user"
    _write_manifest(root_a / "shared@0.1.0", "shared", "0.1.0")
    _write_manifest(root_b / "shared@0.1.0", "shared", "0.1.0")

    packs = scan_multi_packs([root_a, root_b])
    assert len(packs) == 1
    assert packs[0].source_dir == root_a

    # Reverse order → root_b now wins because it's listed first.
    packs = scan_multi_packs([root_b, root_a])
    assert len(packs) == 1
    assert packs[0].source_dir == root_b


def test_scan_packs_records_source_dir(tmp_path: Path) -> None:
    """Single-root primitive tags every returned pack with its root.

    Mode 3 (directory of pack subdirectories) — the classic vendored layout.
    """

    _write_manifest(tmp_path / "solo@0.1.0", "solo", "0.1.0")
    packs = scan_packs(tmp_path)
    assert len(packs) == 1
    assert packs[0].source_dir == tmp_path
    assert packs[0].manifest_path == tmp_path / "solo@0.1.0" / "manifest.yaml"


# ---------------------------------------------------------------------------
# Polymorphic pack_dirs: precise file + index.html-style directory
# ---------------------------------------------------------------------------


def test_scan_packs_precise_file_entry(tmp_path: Path) -> None:
    """Mode 1 — pack_dirs entry points at a specific ``.yaml`` file.

    Lets one directory host several algorithms whose manifests sit
    alongside each other (``main.manifest.yaml``, ``variant.manifest.yaml``).
    """

    # Two manifests in the same folder — distinct names to prove the
    # scanner picks up the one we point at (not the other).
    _write_manifest(tmp_path, "first", "0.1.0")
    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.rename(tmp_path / "first.manifest.yaml")
    _write_manifest(tmp_path, "second", "0.2.0")
    (tmp_path / "manifest.yaml").rename(tmp_path / "second.manifest.yaml")

    packs = scan_packs(tmp_path / "first.manifest.yaml")
    assert len(packs) == 1
    assert packs[0].manifest.name == "first"
    assert packs[0].manifest.version == "0.1.0"
    assert packs[0].manifest_path == tmp_path / "first.manifest.yaml"
    assert packs[0].source_dir == tmp_path / "first.manifest.yaml"


def test_scan_packs_index_html_dir(tmp_path: Path) -> None:
    """Mode 2 — pack_dirs entry is a directory that directly contains
    ``manifest.yaml``. The directory IS the pack; no subdir walk.
    """

    _write_manifest(tmp_path, "hello", "0.1.0")
    # Prove that any sibling files are ignored — only the manifest matters.
    (tmp_path / "run.sh").write_text("#!/bin/sh\necho hi\n")

    packs = scan_packs(tmp_path)
    assert len(packs) == 1
    assert packs[0].manifest.name == "hello"
    assert packs[0].manifest_path == tmp_path / "manifest.yaml"
    assert packs[0].source_dir == tmp_path


def test_scan_packs_dir_name_no_longer_required(tmp_path: Path) -> None:
    """Under the polymorphic contract, the ``name@version`` directory
    naming convention is optional — identity comes from manifest content.
    """

    # Directory named freely; manifest inside still declares its own name.
    freeform = tmp_path / "any-name-i-want"
    _write_manifest(freeform, "actual-name", "1.2.3")

    packs = scan_packs(tmp_path)
    assert len(packs) == 1
    assert packs[0].manifest.name == "actual-name"
    assert packs[0].manifest.version == "1.2.3"


def test_scan_packs_bad_file_suffix_rejected(tmp_path: Path) -> None:
    """A pack_dirs entry pointing at a non-.yaml file is skipped."""

    bogus = tmp_path / "not-a-manifest.txt"
    bogus.write_text("nope")
    assert scan_packs(bogus) == []


def test_scan_multi_packs_mixed_modes(tmp_path: Path) -> None:
    """Three different pack_dirs modes in one list all deliver packs
    into the same catalog with correct ``manifest_path`` on each.
    """

    # Mode 3: dir with subdirs
    legacy_root = tmp_path / "vendored"
    _write_manifest(legacy_root / "vendored-pack@0.1.0", "vendored-pack", "0.1.0")

    # Mode 2: dir with top-level manifest.yaml
    colocated = tmp_path / "colocated-pack"
    _write_manifest(colocated, "colocated-pack", "0.1.0")

    # Mode 1: precise .yaml file (in a shared folder)
    shared = tmp_path / "shared"
    shared.mkdir()
    _write_manifest(shared, "algo-a", "0.1.0")
    (shared / "manifest.yaml").rename(shared / "algo-a.manifest.yaml")

    packs = scan_multi_packs(
        [legacy_root, colocated, shared / "algo-a.manifest.yaml"]
    )
    names = {p.manifest.name for p in packs}
    assert names == {"vendored-pack", "colocated-pack", "algo-a"}

    by_name = {p.manifest.name: p for p in packs}
    assert by_name["vendored-pack"].manifest_path.name == "manifest.yaml"
    assert by_name["colocated-pack"].manifest_path == colocated / "manifest.yaml"
    assert by_name["algo-a"].manifest_path == shared / "algo-a.manifest.yaml"


# ---------------------------------------------------------------------------
# NodeConfig backward compatibility
# ---------------------------------------------------------------------------


def test_load_legacy_packs_dir_scalar(tmp_path: Path) -> None:
    """A config that predates ``pack_dirs`` still loads and behaves.

    The legacy scalar is promoted to a single-entry list; the scalar
    itself is cleared so the next write doesn't carry the deprecated
    key forward.
    """

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"packs_dir": str(tmp_path / "legacy-packs")})
    )
    cfg = load_node_config(cfg_path)
    assert cfg.pack_dirs == [tmp_path / "legacy-packs"]
    assert cfg.packs_dir is None


def test_scalar_prepends_when_both_present(tmp_path: Path) -> None:
    """The legacy scalar wins the front slot — first-wins across dirs
    means the pre-migration behavior (what the scalar pointed at) stays
    the primary root."""

    cfg = NodeConfig.model_validate(
        {
            "packs_dir": str(tmp_path / "legacy"),
            "pack_dirs": [str(tmp_path / "user"), str(tmp_path / "team")],
        }
    )
    assert cfg.pack_dirs == [
        tmp_path / "legacy",
        tmp_path / "user",
        tmp_path / "team",
    ]
    assert cfg.packs_dir is None


def test_scalar_dedup_when_already_in_list(tmp_path: Path) -> None:
    """If the scalar matches an existing pack_dirs entry, position is
    preserved (no duplicate)."""

    shared = tmp_path / "shared"
    cfg = NodeConfig.model_validate(
        {
            "packs_dir": str(shared),
            "pack_dirs": [str(shared), str(tmp_path / "other")],
        }
    )
    assert cfg.pack_dirs == [shared, tmp_path / "other"]


def test_empty_pack_dirs_falls_back_to_default(tmp_path: Path) -> None:
    """Explicit ``pack_dirs: []`` should never leave the node with zero
    roots — the validator restores the default so the scan still works.
    """

    cfg = NodeConfig.model_validate({"pack_dirs": []})
    assert len(cfg.pack_dirs) == 1
