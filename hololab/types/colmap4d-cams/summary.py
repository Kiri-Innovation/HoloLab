"""Read only the unsigned little-endian count headers; never scan records."""
from pathlib import Path

KIND = 'colmap4d-cams'
FILES = (('camera', 'cameras.bin'), ('image', 'images.bin'))


def summary(path, storage):
    if storage != "dir":
        return None
    items = []
    for label, filename in FILES:
        try:
            with (Path(path) / filename).open("rb", buffering=0) as stream:
                header = stream.read(8)
        except OSError:
            continue
        if len(header) == 8:
            items.append({"label": label, "value": int.from_bytes(header, "little")})
    if not items:
        return None
    return {"kind": KIND, "counts": {
        "kind": KIND, "count": items[0]["value"], "items": items,
    }}
