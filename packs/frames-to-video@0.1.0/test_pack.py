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

    def clip(self, name, size, rate=12):
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
                "1",
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

    def test_frames_and_validation(self):
        frames = self.root / "frames"
        frames.mkdir()
        self.execute({"frames": frames}, {"fps": 30}, ok=False)
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(self.src / "clip.mp4"),
                "-frames:v",
                "6",
                str(frames / "frame_%04d.png"),
            ],
            check=True,
        )
        result = self.execute({"frames": frames}, {"fps": 30})
        self.assertEqual(int(result["streams"][0]["nb_frames"]), 6)
        self.assertAlmostEqual(float(result["format"]["duration"]), 0.2, places=2)
        self.execute({"frames": frames}, {"fps": 0}, ok=False)
        (frames / "unexpected.txt").write_text("not an image")
        self.execute({"frames": frames}, {"fps": 30}, ok=False)


if __name__ == "__main__":
    unittest.main()
