"""Real ffmpeg checks: placement, alpha, tall images, timing and odd padding."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PACK = Path(__file__).resolve().parent


def ffmpeg(*args):
    return subprocess.check_output(["ffmpeg", "-v", "error", "-y", *map(str, args)])


class PackTest(unittest.TestCase):
    def test_render_and_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, image = root / "source.mkv", root / "image.pam"
            ffmpeg(
                "-f",
                "lavfi",
                "-i",
                "color=black:size=161x121:rate=12,format=rgb24",
                "-t",
                "1",
                "-c:v",
                "ffv1",
                src,
            )
            # Oversized portrait, half-transparent red, independent of video.
            image.write_bytes(
                b"P7\nWIDTH 300\nHEIGHT 900\nDEPTH 4\nMAXVAL 255\nTUPLTYPE RGB_ALPHA\nENDHDR\n"
                + bytes([255, 0, 0, 128]) * (300 * 900)
            )
            out = root / "out"

            def execute(params, ok=True):
                result = subprocess.run(
                    [sys.executable, str(PACK / "run.py")],
                    text=True,
                    input=json.dumps(
                        {
                            "inputs": {"video": str(src), "image": str(image)},
                            "outputs": {"video": str(out)},
                            "params": params,
                        }
                    ),
                    capture_output=True,
                )
                self.assertEqual(result.returncode == 0, ok, result.stderr)
                return out / "output.mp4"

            for vertical in ["top", "middle", "bottom"]:
                for horizontal in ["left", "center", "right"]:
                    target = execute(
                        {
                            "position": f"{vertical}-{horizontal}",
                            "margin": 10,
                            "width_ratio": 0.1,
                            "opacity": 1,
                        }
                    )
                    data = json.loads(
                        subprocess.check_output(
                            [
                                "ffprobe",
                                "-v",
                                "error",
                                "-show_streams",
                                "-show_format",
                                "-of",
                                "json",
                                str(target),
                            ]
                        )
                    )
                    self.assertEqual(len(data["streams"]), 1)
                    stream = data["streams"][0]
                    self.assertEqual((stream["width"], stream["height"]), (162, 122))
                    self.assertEqual(stream["codec_name"], "h264")
                    self.assertEqual(stream["pix_fmt"], "yuv420p")
                    self.assertEqual(stream["r_frame_rate"], "12/1")
                    self.assertEqual(int(stream["nb_frames"]), 12)
                    self.assertAlmostEqual(float(data["format"]["duration"]), 1, places=2)
                    pixels = ffmpeg(
                        "-ss",
                        "0.8",
                        "-i",
                        target,
                        "-frames:v",
                        "1",
                        "-f",
                        "rawvideo",
                        "-pix_fmt",
                        "rgb24",
                        "-",
                    )
                    coords = [
                        (j % 162, j // 162)
                        for j in range(162 * 122)
                        if pixels[3 * j] > 65 and pixels[3 * j + 1] < 30
                    ]
                    self.assertTrue(coords, "Overlay must remain visible at video end")
                    xs, ys = zip(*coords, strict=True)
                    x = {"left": 10, "center": (161 - 16) / 2, "right": 161 - 16 - 10}[horizontal]
                    y = {"top": 10, "middle": (121 - 48) / 2, "bottom": 121 - 48 - 10}[vertical]
                    self.assertAlmostEqual(min(xs), x, delta=2)
                    self.assertAlmostEqual(min(ys), y, delta=2)
                    self.assertAlmostEqual(max(xs) - min(xs) + 1, 16, delta=2)
                    self.assertLess(max(pixels[::3]), 170)  # Source alpha preserved.
            target = execute({"margin": 10, "width_ratio": 1})
            pixels = ffmpeg(
                "-i", target, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"
            )
            ys = [j // 162 for j in range(162 * 122) if pixels[3 * j] > 65]
            self.assertGreaterEqual(min(ys), 9)
            self.assertLessEqual(max(ys), 112)  # Portrait also fits height.
            target = execute({"opacity": 0})
            pixels = ffmpeg(
                "-i", target, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"
            )
            self.assertLess(max(pixels), 10)
            for params in [
                {"width_ratio": 0},
                {"width_ratio": 2},
                {"margin": 61},
                {"margin": 0.5},
                {"opacity": -1},
                {"opacity": 2},
                {"width_ratio": float("nan")},
                {"position": "bad"},
            ]:
                execute(params, ok=False)


if __name__ == "__main__":
    unittest.main()
