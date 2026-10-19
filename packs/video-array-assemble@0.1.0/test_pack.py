import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from run import run


class AssembleTest(unittest.TestCase):
    def test_order_duplicates_and_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = root / "z clip.mp4"
            b = root / "a clip.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=red:s=32x32:r=2",
                    "-t",
                    "1",
                    "-c:v",
                    "libx264",
                    str(a),
                ],
                check=True,
            )
            b.write_bytes(a.read_bytes())
            bundle = root / "bundle"
            bundle.mkdir()
            (bundle / "a.mp4").symlink_to(b)
            out = root / "out"
            out.mkdir()  # Runtime precreates output roots.

            def invoke(paths):
                run({"params": {"paths_json": json.dumps(paths)}, "outputs": {"videos": str(out)}})

            invoke([str(a), str(bundle), str(a)])
            self.assertEqual(sorted(p.name for p in out.iterdir()), ["0000", "0001", "0002"])
            self.assertEqual((out / "0000" / a.name).resolve(), a)
            self.assertEqual((out / "0001" / b.name).resolve(), b)
            self.assertEqual((out / "0002" / a.name).resolve(), a)
            with self.assertRaisesRegex(ValueError, "empty"):
                invoke([str(a)])
            before = sorted(str(p) for p in out.rglob("*"))
            for bad in [[], {}, [False], ["relative.mp4"], [str(root / "missing.mp4")]]:
                with self.subTest(paths=bad), self.assertRaises(ValueError):
                    invoke(bad)
                self.assertEqual(sorted(str(p) for p in out.rglob("*")), before)
            (bundle / "b.mp4").symlink_to(a)
            with self.assertRaisesRegex(ValueError, "exactly one"):
                invoke([str(bundle)])
            corrupt = root / "corrupt.mp4"
            corrupt.write_text("not video")
            with self.assertRaisesRegex(ValueError, "readable video"):
                invoke([str(corrupt)])


if __name__ == "__main__":
    unittest.main()
