from fractions import Fraction
from pathlib import Path

from _media import encode, main, number


def run(i, p, out):
    image = Path(i["image"])
    if not image.is_file():
        raise ValueError("image must be one image file")
    duration = number(p.get("duration_seconds", 3), "duration_seconds", positive=True)
    fps = number(p.get("fps", 30), "fps", positive=True)
    if fps > 240:
        raise ValueError("fps must be <= 240")
    count = Fraction(str(duration)) * Fraction(str(fps))
    frames = -(-count.numerator // count.denominator)
    encode(
        [
            "-loop",
            "1",
            "-framerate",
            str(fps),
            "-i",
            str(image.resolve()),
            "-frames:v",
            str(frames),
            "-map",
            "0:v:0",
        ],
        out,
        "pad=ceil(iw/2)*2:ceil(ih/2)*2,setsar=1",
    )


if __name__ == "__main__":
    main(run)
