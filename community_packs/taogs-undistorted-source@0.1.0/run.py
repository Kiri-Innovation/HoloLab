import argparse
import json
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--source", required=True)
p.add_argument("--images", required=True)
p.add_argument("--cams", required=True)
a = p.parse_args()
s = Path(a.source)
frames = sorted(x.name for x in (s / "images").iterdir() if not x.name.startswith("."))
assert len(frames) == 50, len(frames)
assert frames == sorted(x.name for x in (s / "cams").iterdir() if not x.name.startswith("."))
for frame in frames:
    images = list((s / "images" / frame).glob("*.png"))
    assert len(images) == 80, (frame, len(images))
    for f in ["cameras.txt", "images.txt"]:
        assert (s / "cams" / frame / f).is_file()
for kind, dest in [("images", a.images), ("cams", a.cams)]:
    out = Path(dest)
    out.mkdir(parents=True, exist_ok=True)
    for frame in frames:
        (out / frame).symlink_to(s / kind / frame, target_is_directory=True)
print(json.dumps({"source": str(s), "frames": 50, "cameras_per_frame": 80, "images": 4000}))
