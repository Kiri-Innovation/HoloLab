"""Integration checks for window boundaries, precedence, offsets and video EOF."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PACK = Path(__file__).resolve().parent


def ffmpeg(*args):
    return subprocess.check_output(["ffmpeg", "-v", "error", "-y", *map(str, args)])


class TimingTest(unittest.TestCase):
    def test_windows_offsets_and_picture_in_picture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base, layer, still = root / "base.mkv", root / "layer.mkv", root / "still.ppm"
            ffmpeg(
                "-f", "lavfi", "-i", "color=black:s=160x120:r=8", "-t", "2", "-c:v", "ffv1", base
            )
            ffmpeg("-f", "lavfi", "-i", "color=red:s=80x80:r=8", "-t", "0.5", "-c:v", "ffv1", layer)
            still.write_bytes(b"P6\n80 80\n255\n" + bytes([255, 0, 0]) * 6400)

            def execute(params, video=False, ok=True):
                pack = PACK.parent / "video-video-overlay@0.1.0" if video else PACK
                inp = {
                    "video": str(base),
                    ("overlay" if video else "image"): str(layer if video else still),
                }
                result = subprocess.run(
                    [sys.executable, str(pack / "run.py")],
                    input=json.dumps(
                        {"inputs": inp, "outputs": {"video": str(root / "out")}, "params": params}
                    ),
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode == 0, ok, result.stderr)
                if not ok:
                    return []
                raw = ffmpeg(
                    "-i",
                    root / "out/output.mp4",
                    "-vsync",
                    "0",
                    "-f",
                    "rawvideo",
                    "-pix_fmt",
                    "rgb24",
                    "-",
                )
                size = 160 * 120 * 3
                self.assertEqual(len(raw), 16 * size)  # Main clip survives overlay EOF.
                return [raw[k : k + size] for k in range(0, len(raw), size)]

            def red(frame):
                return [
                    (k % 160, k // 160)
                    for k in range(160 * 120)
                    if frame[3 * k] > 180 and frame[3 * k + 1] < 30
                ]

            params = {
                "start_seconds": 0.5,
                "end_seconds": 1,
                "duration_seconds": -1,
                "offset_x_ratio": 0.31,
                "offset_y_ratio": 0.27,
                "margin": 0,
            }
            frames = execute(params)
            self.assertEqual([bool(red(f)) for f in frames], [False] * 4 + [True] * 4 + [False] * 8)
            xs, ys = zip(*red(frames[4]), strict=True)
            self.assertAlmostEqual(min(xs), 49.6, delta=2)
            self.assertAlmostEqual(min(ys), 32.4, delta=2)
            fallback = execute({**params, "end_seconds": None, "duration_seconds": 0.5})
            self.assertEqual(frames, fallback)
            frames = execute({"start_seconds": 0.5, "end_seconds": 1.75}, video=True)
            self.assertEqual([bool(red(f)) for f in frames], [False] * 4 + [True] * 4 + [False] * 8)
            frames = execute({})  # Legacy params still mean full duration.
            self.assertTrue(all(red(f) for f in frames))
            frames = execute({"start_seconds": 1, "end_seconds": 20})
            self.assertEqual([bool(red(f)) for f in frames], [False] * 8 + [True] * 8)
            for p in [
                {"end_seconds": 0},
                {"start_seconds": 2},
                {"start_seconds": -1},
                {"duration_seconds": 0},
                {"offset_x_ratio": 1.1},
                {"offset_y_ratio": -0.1},
                {"end_seconds": float("inf")},
            ]:
                execute(p, ok=False)


if __name__ == "__main__":
    unittest.main()
