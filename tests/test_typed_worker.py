import asyncio
import os
import sys
from pathlib import Path

import pytest
import yaml
from PIL import Image as Pixels

from hololab.manifest.render import RenderContext, render_manifest
from hololab.manifest.schema import load_manifest
from hololab.node.executor import ExecPlan, run_subprocess
from hololab.typed_values import Array, Image, RunContext
from hololab.typed_worker import Invocation

IMAGE = dict(tags=["image"], storage="file")
FRAMES = dict(tags=["image"], storage="dir", arrayed=True, dim_labels=["frame"])


def fixture(root, name, source, output):
    folder = root / name
    folder.mkdir()
    path = folder / "manifest.yaml"
    path.write_text(
        yaml.safe_dump(
            dict(
                apiVersion="hololab.dev/v1",
                kind="Algorithm",
                name=f"test/{name}",
                version="0.1.0",
                inputs={"frames": FRAMES},
                outputs=output,
                params={"index": {"type": "int", "default": 0}},
                runtime={"env": "fixture"},
                exec={"typed": {"api": 1, "entry": "adapter.py:apply"}},
            )
        )
    )
    (folder / "adapter.py").write_text(source)
    return path


def dispatch(path, inputs, outputs, scratch):
    manifest, _ = load_manifest(path)
    ctx = RenderContext(
        inputs={k: str(v) for k, v in inputs.items()},
        outputs={k: str(v) for k, v in outputs.items()},
        pack_dir=str(path.parent),
        scratch_dir=str(scratch),
    )
    rendered = render_manifest(manifest, ctx)
    plan = ExecPlan(
        rendered.shell,
        "unused",
        sys.prefix,
        path.parent,
        cached_env={**os.environ, "PATH": f"{Path(sys.executable).parent}:{os.environ['PATH']}"},
    )
    result = asyncio.run(
        run_subprocess(
            plan,
            on_log=lambda *args: None,
            on_progress=lambda *args: None,
            cancel_event=asyncio.Event(),
        )
    )
    assert result.exit_code == 0, result
    return result


def test_two_nodes(tmp_path, monkeypatch):
    reverse = fixture(
        tmp_path,
        "reverse",
        "from hololab.typed_values import Array\n"
        "def apply(*, frames, params, ctx): return Array(tuple(reversed(frames)))",
        {"reversed": FRAMES},
    )
    select = fixture(
        tmp_path,
        "select",
        "def apply(*, frames, params, ctx): return frames[params['index']]",
        {"selected": IMAGE},
    )
    src = tmp_path / "input"
    src.mkdir()
    for name, color in [("b", "red"), ("a", "blue")]:
        Pixels.new("RGB", (3, 3), color).save(src / name, format="PNG")
    mid, final = tmp_path / "intermediate", tmp_path / "result"
    dispatch(reverse, {"frames": src}, {"reversed": mid}, tmp_path / "normal-a")
    dispatch(select, {"frames": mid}, {"selected": final}, tmp_path / "normal-b")
    assert (mid / ".hololab-done").is_file()
    assert final.read_bytes() == (src / "b").read_bytes()

    ctx = RunContext(tmp_path / "compact")
    ctx.scratch.mkdir()
    first, second = Invocation(reverse), Invocation(select)
    with monkeypatch.context() as m:
        m.setattr(Array, "write", lambda *a, **kw: pytest.fail("materialized internal edge"))
        result = first.invoke({"frames": src}, {}, ctx)["reversed"]
        m.setattr(Array, "read", lambda *a, **kw: pytest.fail("read materialized edge"))
        selected = second.invoke({"frames": result}, {}, ctx)["selected"]
        assert selected is result[0]
    assert not list(ctx.scratch.iterdir())
    out = tmp_path / "compact-result"
    selected.write(out, spec=second.manifest.outputs["selected"], io=ctx)
    assert out.read_bytes() == final.read_bytes()

    # Exercise that same object composition in a spawned algorithm interpreter.
    # The two inner adapters keep their ordinary manifest/keyword signatures.
    pair = fixture(
        tmp_path,
        "pair",
        "from pathlib import Path\n"
        "from hololab.typed_worker import Invocation\n"
        "def apply(*, frames, params, ctx):\n"
        "    root = Path(__file__).resolve().parents[1]\n"
        "    a = Invocation(root / 'reverse/manifest.yaml')\n"
        "    b = Invocation(root / 'select/manifest.yaml')\n"
        "    inner = a.invoke({'frames': frames}, {}, ctx)['reversed']\n"
        "    value = b.invoke({'frames': inner}, params, ctx)['selected']\n"
        "    assert value is inner[params['index']]\n"
        "    return value\n",
        {"selected": IMAGE},
    )
    child_out = tmp_path / "pair-out"
    scratch = tmp_path / "pair-scratch"
    dispatch(pair, {"frames": src}, {"selected": child_out}, scratch)
    assert child_out.read_bytes() == final.read_bytes()
    assert not list(scratch.iterdir())


def test_bad_calls(tmp_path):
    path = fixture(
        tmp_path, "broken", "def apply(*, frames, params, ctx): return {}", {"a": IMAGE, "b": IMAGE}
    )
    call = Invocation(path)
    frame = tmp_path / "image"
    Pixels.new("RGB", (1, 1)).save(frame, format="PNG")
    values = {"frames": Array((Image(frame),))}
    ctx = RunContext(tmp_path)
    for params, error in [
        ({"missing": 0}, "unknown parameters"),
        ({"index": True}, "finite"),
        ({}, "return keys"),
    ]:
        with pytest.raises(ValueError, match=error):
            call.invoke(values, params, ctx)
    (path.parent / "adapter.py").write_text("def apply(*, wrong): pass")
    with pytest.raises(ValueError, match="signature"):
        call.invoke(values, {}, ctx)


def test_multiport_publish_rollback(tmp_path, monkeypatch):
    from hololab.typed_worker import run

    pack = fixture(
        tmp_path,
        "multi",
        "def apply(*, frames, params, ctx): return {'a': frames, 'b': frames[0]}",
        {"a": FRAMES, "b": IMAGE},
    )
    frames = tmp_path / "input"
    frames.mkdir()
    Pixels.new("RGB", (2, 2)).save(frames / "im", format="PNG")
    first = tmp_path / "first"
    first.mkdir()  # NodeRuntime pre-creates directory outputs.
    second = tmp_path / "second"
    request = dict(
        inputs={"frames": frames},
        outputs={"a": first, "b": second},
        scratch=tmp_path / "scratch",
        params={},
    )
    replace = Path.replace

    def fail_second(path, target):
        if target == second:
            raise OSError("injected publish failure")
        return replace(path, target)

    with monkeypatch.context() as m:
        m.setattr(Path, "replace", fail_second)
        with pytest.raises(OSError, match="publish failure"):
            run(pack, request)
    assert list(first.iterdir()) == []
    assert not second.exists()
    run(pack, request)
    assert (first / ".hololab-done").is_file()
    assert second.read_bytes() == (frames / "im").read_bytes()


@pytest.mark.parametrize(
    "bad",
    [
        {"typed": {"api": 1, "entry": "../adapter.py:apply"}},
        {"typed": {"api": 2, "entry": "adapter.py:apply"}},
        {"typed": {"api": 1, "entry": "adapter.py:apply"}, "shell": "true"},
    ],
)
def test_manifest_rejects_invalid_typed_entry(tmp_path, bad):
    path = fixture(tmp_path, "invalid", "", {"out": IMAGE})
    raw = yaml.safe_load(path.read_text())
    raw["exec"] = bad
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError):
        load_manifest(path)
