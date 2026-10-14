"""Encode the 93 identical-camera comparisons and frozen-model camera orbit."""

import subprocess

from PIL import Image, ImageDraw, ImageFont
from run_experiment import OUT

width, height = Image.open(OUT / "frozen_t050/renders/000.png").size
font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
command = [
    "ffmpeg",
    "-y",
    "-loglevel",
    "error",
    "-f",
    "rawvideo",
    "-pixel_format",
    "rgb24",
    "-video_size",
    f"{3 * width}x{height + 36}",
    "-framerate",
    "15",
    "-i",
    "-",
    "-an",
    "-c:v",
    "libx264",
    "-crf",
    "20",
    "-pix_fmt",
    "yuv420p",
    str(OUT / "comparison_orbit.mp4"),
]
with subprocess.Popen(command, stdin=subprocess.PIPE) as process:
    for i in range(93):
        canvas = Image.new("RGB", (3 * width, height + 36), (22, 29, 38))
        draw = ImageDraw.Draw(canvas)
        for j, (name, label) in enumerate(
            [
                ("old_static", "Old static 15k"),
                ("static30k", "Static 30k control"),
                ("frozen_t050", "4D frozen t=0.50"),
            ]
        ):
            frame = Image.open(OUT / name / "renders" / f"{i:03d}.png").convert("RGB")
            assert frame.size == (width, height)
            canvas.paste(frame, (width * j, 36))
            draw.text((width * j + 12, 7), label, font=font, fill="white")
        process.stdin.write(canvas.tobytes())
    process.stdin.close()
    assert process.wait() == 0
subprocess.run(
    [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-framerate",
        "15",
        "-i",
        str(OUT / "frozen_t050/renders/%03d.png"),
        "-c:v",
        "libx264",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        str(OUT / "frozen_orbit.mp4"),
    ],
    check=True,
)
