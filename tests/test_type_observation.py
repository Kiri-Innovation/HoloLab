from hololab.gateway.handle_summary import HandleSummaryCache, summarize_handle
from hololab.gateway.handles import Handle
from hololab.gateway.models import LatestOutputHandleOut
from hololab.gateway.type_observation import RuntimeObservation


def handle(path, tags=("colmap-cams",)):
    return Handle(
        handle_id="h",
        node_id="device",
        storage="dir",
        path=str(path),
        tags=list(tags),
        size_bytes=0,
        output_port_name="out",
    )


def cams(path, count):
    path.mkdir(parents=True, exist_ok=True)
    (path / "images.txt").write_text(
        f"# Number of images: {count}, mean observations per image: 4\n"
    )
    (path / "cameras.txt").write_text(
        "# CAMERA_ID MODEL WIDTH HEIGHT PARAMS\n1 PINHOLE 640 480 1 1 1 1\n"
    )


def test_sampled_counts_and_complete_directory_axis(tmp_path):
    cams(tmp_path / "f0", 21)
    cams(tmp_path / "f1", 9)
    s = summarize_handle(handle(tmp_path), depth=1, dim_labels=["frame"])
    assert s["dim_sizes"] == [2]
    assert s["internal_count_items"] == [
        {"label": "pose", "value": 21},
        {"label": "intr", "value": 1},
    ]
    runtime = RuntimeObservation.model_validate(s["runtime_shape"])
    assert runtime.axes.evidence.status == "complete"
    assert runtime.axes.evidence.value[0].size.evidence.value == 2
    pose = runtime.internalCounts[0].count.evidence
    assert pose.status == "sampled" and pose.sample == "f0" and pose.value == 21
    assert str(tmp_path) not in str(s["runtime_shape"])


def test_ragged_image_collection_does_not_claim_uniform_dimensions(tmp_path):
    for name, count in [("f0", 21), ("f1", 7)]:
        leaf = tmp_path / name
        leaf.mkdir()
        for i in range(count):
            (leaf / f"cam_{i}.png").touch()
    s = summarize_handle(handle(tmp_path, ("image",)), depth=2, dim_labels=["frame", "cam"])
    # Ragged collections have no uniform rectangular shape; never sample the
    # first camera count and pretend it describes every frame.
    assert "dim_sizes" not in s
    axes = s["runtime_shape"]["axes"]["evidence"]
    assert axes["status"] == "complete"
    assert axes["value"][0]["size"]["evidence"]["value"] == 2
    assert axes["value"][1]["size"]["evidence"] == {
        "status": "unknown",
        "reason": "ragged",
    }
    assert axes["value"][1]["sizeRange"]["evidence"]["value"] == {
        "min": 7,
        "max": 21,
    }


def test_scalar_counts_complete_and_missing_handle_unknown(tmp_path):
    cams(tmp_path, 0)
    s = summarize_handle(handle(tmp_path), depth=0)
    assert s["runtime_shape"]["internalCounts"][0]["count"]["evidence"] == {
        "status": "complete",
        "value": 0,
        "source": "tag-internal-count-probe",
    }
    assert s["runtime_shape"]["axes"]["evidence"]["status"] == "unknown"
    missing = summarize_handle(handle(tmp_path / "absent"), depth=1)
    assert missing["runtime_shape"]["axes"]["evidence"]["reason"] == "handle-unavailable"
    assert missing["runtime_shape"]["internalCounts"] == []


def test_cache_scope_and_typed_legacy_wire_projection(tmp_path):
    cams(tmp_path / "f0", 21)
    h = handle(tmp_path)
    cache = HandleSummaryCache()
    unlabelled = cache.get_or_compute(h, dim_labels=None)
    labelled = cache.get_or_compute(h, dim_labels=["frame"])
    assert unlabelled["runtime_shape"]["axes"]["evidence"]["status"] == "unknown"
    assert labelled["runtime_shape"]["axes"]["evidence"]["value"][0]["label"] == "frame"
    out = LatestOutputHandleOut(handle_id="h", tags=h.tags, **labelled).model_dump()
    assert out["internal_count_items"] == labelled["internal_count_items"]
    assert out["runtime_shape"] == labelled["runtime_shape"]
    cache.invalidate("h")
    assert cache.size() == 0


def test_ragged_directory_publishes_bounds_not_rectangular_shape(tmp_path):
    for relative in ["a/one", "b/one", "b/two"]:
        cams(tmp_path / relative, 21)
    s = summarize_handle(handle(tmp_path), depth=2, dim_labels=["outer", "inner"])
    assert s.get("dim_sizes") is None
    axes = s["runtime_shape"]["axes"]["evidence"]
    assert axes["status"] == "complete"
    assert axes["value"][0]["size"]["evidence"]["value"] == 2
    assert axes["value"][1]["sizeRange"]["evidence"]["value"] == {
        "min": 1,
        "max": 2,
    }


def test_http_summary_preserves_legacy_counts_and_exposes_evidence(tmp_path):
    from fastapi.testclient import TestClient

    from hololab.gateway.app import create_app

    root = tmp_path / "artifact"
    cams(root, 21)
    with TestClient(create_app(db_path=tmp_path / "api.sqlite")) as client:

        async def seed():
            await client.app.state.handles.register(handle(root))

        client.portal.call(seed)
        response = client.get("/api/handles/h/summary")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["internal_count_items"] == [
            {"label": "pose", "value": 21},
            {"label": "intr", "value": 1},
        ]
        evidence = body["runtime_shape"]["internalCounts"][0]["count"]["evidence"]
        assert evidence["status"] == "complete" and evidence["value"] == 21
        assert body["runtime_shape"]["schemaVersion"] == 1
