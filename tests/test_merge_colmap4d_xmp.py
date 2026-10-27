from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(REPO_ROOT / "packages" / "colmap4d" / "src"))
PACK = REPO_ROOT / "hololab" / "packs" / "merge-colmap4d@0.1.4" / "merge_colmap4d.py"
SPEC = importlib.util.spec_from_file_location("merge_colmap4d_014", PACK)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _jpeg(path: Path, *, alias: str = "cam0", omit: str | None = None) -> None:
    values = {
        "aeMode": "MANUAL",
        "alias": alias,
        "capId": "capture-id",
        "clockAgeMs": "1",
        "clockOffsetMs": "2",
        "decodedIndex": "3",
        "exposureNs": "3000000",
        "focusDistance": "1.0",
        "iso": "100",
        "sensorTimestampNs": "1000",
        "source": "sei",
        "syncVersion": "7",
        "timebase": "server_epoch",
        "wallClockEpochNs": "1000000000",
    }
    if omit is not None:
        values.pop(omit)
    attributes = " ".join(f'hololab:{key}="{value}"' for key, value in values.items())
    xml = (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns/">'
        f'<rdf:Description xmlns:hololab="{MODULE.HOLOLAB_XMP_NS}" {attributes}/>'
        "</rdf:RDF></x:xmpmeta>"
    ).encode()
    payload = MODULE.XMP_APP1_HEADER + xml
    segment = b"\xff\xe1" + (len(payload) + 2).to_bytes(2, "big") + payload
    path.write_bytes(b"\xff\xd8" + segment + b"\xff\xda\x00\x02scan\xff\xd9")


def test_mid_exposure_timestamp_and_timebase(tmp_path: Path) -> None:
    path = tmp_path / "cam0.jpg"
    _jpeg(path)
    metadata = MODULE._image_metadata(path)
    assert metadata["t_ns"] == 1_001_500_000
    assert metadata["timebase"] == "server_epoch"


def test_missing_hololab_field_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "cam0.jpg"
    _jpeg(path, omit="wallClockEpochNs")
    with pytest.raises(ValueError, match="wallClockEpochNs"):
        MODULE._image_metadata(path)


def test_alias_must_match_filename(tmp_path: Path) -> None:
    path = tmp_path / "cam0.jpg"
    _jpeg(path, alias="cam1")
    with pytest.raises(ValueError, match="does not match filename"):
        MODULE._image_metadata(path)
