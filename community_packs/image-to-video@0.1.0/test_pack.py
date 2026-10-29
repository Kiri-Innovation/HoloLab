"""Still duration, odd padding and silent output using real ffmpeg."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PACK = Path(__file__).resolve().parent


class ImageTest(unittest.TestCase):
    def test_still(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "image.png"
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=red:s=101x79,format=rgb24",
                    "-frames:v",
                    "1",
                    str(image),
                ],
                check=True,
            )
            for params, ok in [
                ({"duration_seconds": 3, "fps": 8}, True),
                ({"duration_seconds": 0}, False),
                ({"fps": float("nan")}, False),
            ]:
                r = subprocess.run(
                    [sys.executable, str(PACK / "run.py")],
                    input=json.dumps(
                        {
                            "inputs": {"image": str(image)},
                            "params": params,
                            "outputs": {"video": str(root / "out")},
                        }
                    ),
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(r.returncode == 0, ok, r.stderr)
                if not ok:
                    continue
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
                            str(root / "out/output.mp4"),
                        ]
                    )
                )
                self.assertEqual(len(data["streams"]), 1)
                s = data["streams"][0]
                self.assertEqual((s["width"], s["height"]), (102, 80))
                self.assertEqual(s["nb_frames"], "24")
                self.assertEqual(s["pix_fmt"], "yuv420p")
                self.assertEqual(s["codec_name"], "h264")
                self.assertAlmostEqual(float(data["format"]["duration"]), 3)


if __name__ == "__main__":
    unittest.main()
