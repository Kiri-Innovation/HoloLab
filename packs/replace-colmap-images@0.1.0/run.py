"""Replace every image by exact stem, preserve cameras, points and mask semantics."""

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from PIL import Image

p = argparse.ArgumentParser()
for k in ["colmap", "images", "output"]:
    p.add_argument("--" + k, type=Path, required=True)
a = p.parse_args()
a.output.mkdir(parents=True, exist_ok=True)
original = {
    x.stem: x for x in (a.colmap / "images").iterdir() if x.is_file() and not x.name.startswith(".")
}
fixed = {}
for x in a.images.rglob("*"):
    if x.is_file() and not any(v.startswith(".") for v in x.relative_to(a.images).parts):
        try:
            with Image.open(x) as im:
                im.verify()
        except Exception:
            continue
        # fan-out file output names are keyed by source image stem; directories may carry that key.
        keys = [x.stem, *[q for q in x.relative_to(a.images).parts[:-1]]]
        match = [k for k in keys if k in original]
        if len(set(match)) != 1:
            raise ValueError(f"Cannot align {x}: {match}")
        key = match[0]
        if key in fixed:
            raise ValueError("Duplicate stem " + key)
        fixed[key] = x
assert original.keys() == fixed.keys(), (
    sorted(original.keys() - fixed.keys()),
    sorted(fixed.keys() - original.keys()),
)
shutil.copytree(a.colmap / "sparse", a.output / "sparse", dirs_exist_ok=True)
(a.output / "images").mkdir(exist_ok=True)
rows = []
for stem, src in sorted(original.items()):
    with Image.open(src) as oi, Image.open(fixed[stem]) as fi:
        assert oi.size == fi.size, (stem, oi.size, fi.size)
        fi.convert("RGB").save(a.output / "images" / src.name)
    rows.append(
        dict(
            stem=stem,
            source=str(fixed[stem]),
            sha256=hashlib.sha256((a.output / "images" / src.name).read_bytes()).hexdigest(),
        )
    )
(a.output / "replacement.json").write_text(
    json.dumps(
        dict(
            count=len(rows),
            images=rows,
            policy="All supervision replaced; COLMAP unchanged; external masks reused by stem",
        ),
        indent=2,
    )
)
print(f"Replaced {len(rows)} images; original sparse model copied unchanged")
