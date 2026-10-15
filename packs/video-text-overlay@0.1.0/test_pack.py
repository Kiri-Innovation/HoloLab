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

    def test_literal_text_and_validation(self):
        self.execute(
            {"video": self.src},
            {
                "text": "Title: 'quoted' 100% $HOME \nline two",
                "font_size": 16,
                "position": "top-center",
            },
        )
        for position in [
            "top-left",
            "top-right",
            "middle-left",
            "middle-center",
            "middle-right",
            "bottom-left",
            "bottom-center",
            "bottom-right",
        ]:
            self.execute(
                {"video": self.src}, {"text": "Test", "font_size": 12, "position": position}
            )
        self.execute(
            {"video": self.src}, {"text": "", "font_size": 16, "position": "top-center"}, ok=False
        )


if __name__ == "__main__":
    unittest.main()
