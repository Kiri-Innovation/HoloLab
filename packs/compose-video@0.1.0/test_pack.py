"""Real ffmpeg coverage of timing, z-order, defaults, clipping and contracts."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PACK = Path(__file__).resolve().parent


def ffmpeg(*args):
    return subprocess.check_output(["ffmpeg", "-v", "error", "-y", *map(str, args)])


class ComposeTest(unittest.TestCase):
    def test_composition(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            videos, offsets, transforms = [
                root / name for name in ["videos", "offsets", "transforms"]
            ]
            for folder in [videos, offsets, transforms]:
                folder.mkdir()
            for ident, color, duration, size in [
                ("0000", "red", 1, "160x120"),
                ("0001", "blue", 0.5, "80x80"),
                ("0002", "lime", 0.5, "80x80"),
            ]:
                d = videos / ident
                d.mkdir()
                ffmpeg(
                    "-f",
                    "lavfi",
                    "-i",
                    f"color={color}:s={size}:r=8",
                    "-t",
                    duration,
                    "-c:v",
                    "ffv1",
                    d / "video.mkv",
                )
                (offsets / ident).write_text("0" if ident == "0000" else "1.5")
                (transforms / ident).write_text("[0,0,1]" if ident == "0000" else "[0.5,0.25,0.5]")

            # Audio longer than video must not lengthen the visual layer.
            ffmpeg(
                "-i",
                videos / "0000/video.mkv",
                "-f",
                "lavfi",
                "-t",
                "2",
                "-i",
                "anullsrc=r=8000:cl=mono",
                "-c:v",
                "copy",
                "-c:a",
                "pcm_s16le",
                root / "with-audio.mkv",
            )
            (root / "with-audio.mkv").replace(videos / "0000/video.mkv")

            def run(inputs, ok=True):
                result = subprocess.run(
                    [sys.executable, str(PACK / "run.py")],
                    input=json.dumps(
                        {
                            "inputs": {k: str(v) for k, v in inputs.items()},
                            "params": {},
                            "outputs": {"video": str(root / "out")},
                        }
                    ),
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(result.returncode == 0, ok, result.stderr)
                if not ok:
                    self.assertIn("Video pack:", result.stderr)
                    return
                raw = ffmpeg(
                    "-i", root / "out/output.mp4", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"
                )
                n = 160 * 120 * 3
                return [raw[k : k + n] for k in range(0, len(raw), n)]

            inputs = {"videos": videos, "offsets": offsets, "transforms": transforms}
            frames = run(inputs)
            self.assertEqual(len(frames), 16)  # Last layer outlives the base.

            def pixel(frame, x, y):
                return tuple(frame[(y * 160 + x) * 3 : (y * 160 + x) * 3 + 3])

            self.assertGreater(pixel(frames[0], 100, 40)[0], 200)
            self.assertLess(max(frames[9]), 10)  # Gap stays black, no frozen base.
            self.assertGreater(pixel(frames[13], 100, 40)[1], 200)  # Later green wins over blue.
            self.assertLess(max(pixel(frames[13], 20, 20)), 10)
            frames = run({"videos": videos})
            self.assertEqual(len(frames), 8)
            self.assertGreater(pixel(frames[0], 80, 60)[1], 200)
            self.assertGreater(pixel(frames[6], 80, 60)[0], 200)  # Base returns after top EOF.
            (transforms / "0002").write_text("[-0.2,-0.2,1]")
            frames = run(inputs)
            self.assertGreater(pixel(frames[13], 0, 0)[1], 200)  # Off-canvas clipping.
            for bad in [
                "[0,0]",
                "[0,0,0]",
                "[0,0,-1]",
                "[true,0,1]",
                '["0",0,1]',
                "[0,NaN,1]",
                "{}",
                "[0,1e999,1]",
            ]:
                (transforms / "0002").write_text(bad)
                run(inputs, False)
            (transforms / "0002").write_text("[0,0,1]")
            for bad in ["NaN", "inf", "1 2", "-1", "1_0", "true", "1e999"]:
                (offsets / "0002").write_text(bad)
                run(inputs, False)
            (offsets / "0002").write_text("0")
            (offsets / "0002").rename(offsets / "other")
            run(inputs, False)  # Same count is insufficient: IDs must match.
            (offsets / "other").unlink()
            run(inputs, False)


if __name__ == "__main__":
    unittest.main()
