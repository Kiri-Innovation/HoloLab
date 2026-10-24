import json

import pytest

from hololab.pack_registry import Index, RegistryError, SourceUnavailable, find_release, read_index


def test_static_source_and_absence(tmp_path):
    (tmp_path / "index.json").write_text(Index(authority="central").model_dump_json())
    assert read_index(tmp_path.as_uri()).authority == "central"
    with pytest.raises(RegistryError, match="找不到"):
        find_release("demo@0.1.0", [tmp_path.as_uri()])
    with pytest.raises(SourceUnavailable, match="源不可达"):
        read_index((tmp_path / "absent").as_uri())
    (tmp_path / "index.json").write_text(json.dumps({"format": 99, "authority": "x"}))
    with pytest.raises(ValueError):
        read_index(tmp_path.as_uri())
