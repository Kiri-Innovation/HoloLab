"""Run with python -m unittest discover -s <pack-directory>. Requires ffmpeg."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PACK = Path(__file__).resolve().parent


class PackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.src = self.clip("source", "160x120")

    def clip(self, name, size, rate=12, duration=1):
        folder = self.root / name
        folder.mkdir()
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"testsrc2=size={size}:rate={rate}",
                "-t",
                str(duration),
                "-c:v",
                "libx264",
                "-threads",
                "1",
                "-pix_fmt",
                "yuv420p",
                str(folder / "clip.mp4"),
            ],
            check=True,
        )
        return folder

    def execute(self, inputs, params, ok=True):
        out = self.root / "out"
        payload = {
            "inputs": {k: str(v) for k, v in inputs.items()},
            "params": params,
            "outputs": {"video": str(out)},
        }
        result = subprocess.run(
            [sys.executable, str(PACK / "run.py")],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
        )
        if not ok:
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Video pack:", result.stderr)
            return result.stderr
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
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
                    str(out / "output.mp4"),
                ]
            )
        )
        self.assertEqual(len(data["streams"]), 1)  # Intentionally video-only.
        self.assertEqual(data["streams"][0]["codec_name"], "h264")
        return data

    def test_trim_and_invalid_ranges(self):
        data = self.execute({"video": self.src}, {"start_seconds": 0.25, "duration_seconds": 0.5})
        self.assertAlmostEqual(float(data["format"]["duration"]), 0.5, places=2)
        for start, duration in [(-1, 1), (0, 0), (2, 1), (0, float("nan"))]:
            self.execute(
                {"video": self.src},
                {"start_seconds": start, "duration_seconds": duration},
                ok=False,
            )

    def test_end_overrides_duration(self):
        for duration in (5, 28, 0):
            data = self.execute(
                {"video": self.src},
                {"start_seconds": 0.25, "end_seconds": 0.75, "duration_seconds": duration},
            )
            self.assertAlmostEqual(float(data["format"]["duration"]), 0.5, places=2)

    def test_invalid_end(self):
        for end in (0.25, 0, -1, float("nan"), float("inf")):
            error = self.execute(
                {"video": self.src}, {"start_seconds": 0.25, "end_seconds": end}, ok=False
            )
            self.assertIn("end_seconds", error)

    def test_end_past_video(self):
        data = self.execute({"video": self.src}, {"start_seconds": 0.25, "end_seconds": 10})
        self.assertAlmostEqual(float(data["format"]["duration"]), 0.75, places=2)

    def test_defaults_and_null_end(self):
        src = self.clip("long", "160x120", duration=6)
        for params in ({}, {"end_seconds": None}, {"duration_seconds": 5}):
            data = self.execute({"video": src}, params)
            self.assertAlmostEqual(float(data["format"]["duration"]), 5, places=2)
        data = self.execute({"video": self.src}, {"end_seconds": None, "duration_seconds": 0.5})
        self.assertAlmostEqual(float(data["format"]["duration"]), 0.5, places=2)


if __name__ == "__main__":
    unittest.main()
