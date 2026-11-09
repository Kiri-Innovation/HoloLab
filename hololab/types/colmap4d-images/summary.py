"""Count sequence names without opening or decoding images."""
import re
from collections import Counter

FRAME = re.compile(r"^f(\d+)__cam(\d+)\.(png|jpe?g|webp)$", re.I)


def summary(path, storage):
    if storage != "dir":
        return None
    directory = path / "images" if (path / "images").is_dir() else path
    counts = Counter()
    frames = set()
    for entry in directory.iterdir():
        match = FRAME.fullmatch(entry.name)
        if match and entry.is_file():
            frames.add(int(match[1]))
            counts[f"cam{int(match[2])}"] += 1
    return {"kind": "colmap4d-images", "fields": {
        "frame_count": len(frames), "camera_count": len(counts),
        "images_per_camera": dict(sorted(counts.items())),
        "image_count": sum(counts.values()),
    }}
