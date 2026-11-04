"""Exercise the real JPEG endpoint, not a mock returning the source PNG."""
import hashlib
import io
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from hololab.node.fileserver import create_fileserver_app

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")


def legacy_thumbnail(source: Path, output: Path, unpadded: bool, video: bool = False):
    vf = "scale=64:48:force_original_aspect_ratio=decrease"
    # ffmpeg 4.2 lacks force_divisible_by; the fileserver uses a second
    # scale pass to keep the natural-aspect poster dimensions even.
    vf += ",scale=trunc(iw/2)*2:trunc(ih/2)*2" if unpadded else ",pad=64:48:(ow-iw)/2:(oh-ih)/2:color=black"
    subprocess.run([
        "ffmpeg", "-nostdin", "-loglevel", "error",
        *(["-ss", "0.000"] if video else []), "-i", str(source),
        "-frames:v", "1", "-vf", vf, "-q:v", "3", "-y", str(output),
    ], check=True, capture_output=True)
    return output.read_bytes()


@pytest.mark.parametrize("unpadded", [False, True])
@pytest.mark.parametrize("kind", ["rgb-png", "jpeg", "opaque-rgba", "video"])
def test_opaque_thumbnails_are_pixel_identical(tmp_path, kind, unpadded):
    source = tmp_path / ("source.mp4" if kind == "video" else "source.jpg" if kind == "jpeg" else "source.png")
    if kind == "video":
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i",
                        "testsrc2=size=96x64:rate=2", "-t", "1", "-c:v", "libx264", "-y", str(source)],
                       check=True, capture_output=True)
    else:
        image = Image.new("RGB", (96, 64))
        image.putdata([(x * 2, y * 3, (x + y) % 256) for y in range(64) for x in range(96)])
        (image.convert("RGBA") if kind == "opaque-rgba" else image).save(source)
    old = legacy_thumbnail(source, tmp_path / "legacy.jpg", unpadded, kind == "video")
    with TestClient(create_fileserver_app(workspace_root=tmp_path)) as client:
        response = client.get(f"/_thumb/64x48/{source.name}", params={"at": 0, "unpadded": unpadded})
    assert response.status_code == 200, response.text
    before, after = Image.open(io.BytesIO(old)), Image.open(io.BytesIO(response.content))
    assert before.size == after.size
    assert before.tobytes() == after.tobytes()
    assert old == response.content


@pytest.mark.parametrize("unpadded", [False, True])
@pytest.mark.parametrize("mode", ["RGBA", "LA", "P"])
def test_transparency_is_visible_in_jpeg(tmp_path, unpadded, mode):
    image = Image.new("RGBA", (64, 32), (80, 80, 80, 0))
    for y in range(32):
        for x in range(32, 64):
            image.putpixel((x, y), (80, 80, 80, 128 if x < 48 else 255))
    image = image.convert(mode)
    image.save(tmp_path / "source.png")
    with TestClient(create_fileserver_app(workspace_root=tmp_path)) as client:
        response = client.get("/_thumb/64x48/source.png", params={"at": 0, "unpadded": unpadded})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/jpeg"
    jpeg = Image.open(io.BytesIO(response.content)).convert("RGB")
    y = 4 if unpadded else 12
    assert jpeg.size == (64, 32 if unpadded else 48)
    for x, expected in [(4, 216), (12, 176), (36, 148), (44, 128), (52, 80)]:
        assert all(abs(channel - expected) <= 5 for channel in jpeg.getpixel((x, y)))
    if not unpadded:
        assert max(jpeg.getpixel((4, 3))) <= 2  # Existing black letterbox.


def test_old_cache_and_date_validator_cannot_hide_new_rendering(tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGBA", (64, 32), (0, 0, 0, 0)).save(source)
    old_key = f"{source.resolve()}|{source.stat().st_mtime_ns}|64x48|0.000"
    digest = hashlib.sha256(old_key.encode()).hexdigest()[:32]
    cache = tmp_path / ".hololab-thumbs"
    cache.mkdir()
    (cache / f"{digest}.jpg").write_bytes(b"stale legacy thumbnail")
    with TestClient(create_fileserver_app(workspace_root=tmp_path)) as client:
        url = "/_thumb/64x48/source.png?at=0"
        first = client.get(url)
        assert first.status_code == 200
        assert first.headers["etag"] != f'"{digest}"'
        for headers in [
            {"If-None-Match": f'"{digest}"', "If-Modified-Since": first.headers["last-modified"]},
            {"If-Modified-Since": first.headers["last-modified"]},
        ]:
            response = client.get(url, headers=headers)
            assert response.status_code == 200
            assert response.content == first.content
        assert client.get(url, headers={"If-None-Match": first.headers["etag"]}).status_code == 304
