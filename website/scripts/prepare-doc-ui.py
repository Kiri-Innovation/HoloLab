"""Crop/combine reviewed 2x screenshots and encode bounded WebP assets (Pillow)."""

import json
from pathlib import Path

from PIL import Image

source = Path("test-results/doc-ui")
out = Path("public/images/docs")
out.mkdir(parents=True, exist_ok=True)
manifest = []
for theme in ["light", "dark"]:

    def load(name, theme=theme):
        return Image.open(source / f"{name}-{theme}.png").convert("RGB")

    def paired(left, right, left_height=None, right_height=None):
        a, b = load(left), load(right)
        if left_height:
            a = a.crop((0, 0, a.width, min(a.height, left_height)))
        if right_height:
            b = b.crop((0, 0, b.width, min(b.height, right_height)))
        bg = a.getpixel((0, 0))
        pad = 32
        result = Image.new(
            "RGB", (a.width + b.width + pad * 3, max(a.height, b.height) + pad * 2), bg
        )
        result.paste(a, (pad, pad))
        result.paste(b, (a.width + 2 * pad, pad))
        return result

    images = {
        name: load(name) for name in ["gallery", "canvas", "ports", "run", "artifacts", "preview"]
    }
    images["panels"] = paired("packs-part", "devices-part", left_height=600)
    images["add-node"] = paired("packs-part", "node-part", left_height=600)
    images["runs-jobs"] = paired("runs-part", "jobs-part", left_height=650, right_height=650)
    for name, image in images.items():
        file = out / f"{name}-{theme}.webp"
        limit = (400 if name == "canvas" else 200) * 1024
        for quality in [86, 82, 78, 74, 70]:
            image.save(file, format="WEBP", quality=quality, method=6)
            if file.stat().st_size <= limit:
                break
        assert file.stat().st_size <= limit, file
        manifest.append(
            {
                "file": file.name,
                "width": image.width,
                "height": image.height,
                "bytes": file.stat().st_size,
                "quality": quality,
                "captureScale": 2,
            }
        )
(out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(json.dumps(manifest, indent=2))
print("Total bytes:", sum(m["bytes"] for m in manifest))
