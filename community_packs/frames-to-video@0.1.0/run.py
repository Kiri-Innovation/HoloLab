import tempfile
from pathlib import Path

from _media import encode, entries, main, number, probe


def run(i, p, out):
    frames = entries(i["frames"])
    if not frames:
        raise ValueError("frames must not be empty")
    fps = number(p["fps"], "fps", positive=True)
    sizes = []
    for frame in frames:
        if not frame.is_file() or frame.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            raise ValueError("Expected a flat collection of PNG/JPEG image files")
        s, _ = probe(frame)
        sizes.append((s["width"], s["height"]))
    if len(set(sizes)) != 1:
        raise ValueError("All frames must have the same dimensions")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        lines = []
        for index, frame in enumerate(frames):
            name = f"frame_{index:08d}{frame.suffix.lower()}"
            (tmp / name).symlink_to(frame.resolve())
            lines += [f"file '{name}'", f"option framerate {fps}", f"duration {1 / fps:.12f}"]
        lines.append(f"file '{name}'")
        listing = tmp / "frames.txt"
        listing.write_text("\n".join(lines) + "\n")
        encode(
            [
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(listing),
                "-r",
                str(fps),
                "-frames:v",
                str(len(frames)),
                "-map",
                "0:v:0",
            ],
            out,
            "pad=ceil(iw/2)*2:ceil(ih/2)*2,setsar=1",
        )


if __name__ == "__main__":
    main(run)
