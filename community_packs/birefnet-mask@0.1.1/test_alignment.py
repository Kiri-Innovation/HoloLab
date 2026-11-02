"""Real decoder equivalence and private scratch lifetime; no GPU/model required."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('birefnet_pack', Path(__file__).with_name('run.py'))
pack = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pack)
EXTRACTOR = ROOT / 'hololab/builtin_packs/frame-extraction@0.2.0/extract_frames.py'


class AlignmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.video = self.root / 'test.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                        'testsrc2=size=96x64:rate=6', '-frames:v', '12',
                        '-c:v', 'libx264', '-threads', '1', str(self.video)], check=True)

    def assert_matches_builtin(self, video, expected_count, **selection):
        reference = self.root / 'fx'
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        cmd = [sys.executable, str(EXTRACTOR), str(video), '--output', str(reference)]
        for key, value in selection.items():
            cmd += ['--' + key.replace('_', '-'), str(value)]
        subprocess.run(cmd, env=env, check=True, stdout=subprocess.DEVNULL)
        scratch = self.root / 'scratch'
        with pack.decoded_frames(video, scratch, ROOT, **selection) as (frames, count, _):
            self.assertEqual(count, expected_count)
            actual = sorted(frames.glob('*.png'))
            expected = sorted(reference.glob('*.png'))
            self.assertEqual([p.name for p in actual], [p.name for p in expected])
            for left, right in zip(actual, expected, strict=True):
                with Image.open(left) as im, Image.open(right) as ref:
                    self.assertEqual(im.size, ref.size)
                    np.testing.assert_array_equal(np.asarray(im.convert('RGB')),
                                                  np.asarray(ref.convert('RGB')))
        self.assertFalse(frames.exists())
        self.assertEqual(list(scratch.iterdir()), [])

    def test_default_selection(self):
        self.assert_matches_builtin(self.video, 12)

    def test_window_stride_limit_and_resize(self):
        self.assert_matches_builtin(self.video, 3, max_frames=3, skip=2,
                                    start_frame=3, end_frame=11, max_width=48)

    def test_overlapping_invocations_are_isolated_and_cleanup_on_error(self):
        scratch = self.root / 'scratch'
        with pack.decoded_frames(self.video, scratch, ROOT, max_frames=2) as (first, _, _):
            before = {p.name: p.read_bytes() for p in first.glob('*.png')}
            with self.assertRaisesRegex(RuntimeError, 'inference failure'):
                with pack.decoded_frames(self.video, scratch, ROOT, max_frames=3) as (second, _, _):
                    self.assertNotEqual(first, second)
                    self.assertTrue(first.exists() and second.exists())
                    raise RuntimeError('inference failure')
            self.assertFalse(second.exists())
            self.assertEqual(before, {p.name: p.read_bytes() for p in first.glob('*.png')})
        self.assertFalse(first.exists())
        self.assertEqual(list(scratch.iterdir()), [])

    @unittest.skipUnless(os.environ.get('BIREFNET_TEST_VIDEO'), 'set BIREFNET_TEST_VIDEO for real data')
    def test_real_video_50_frames(self):
        self.assert_matches_builtin(Path(os.environ['BIREFNET_TEST_VIDEO']), 50,
                                    max_frames=50, skip=1, start_frame=0, end_frame=0, max_width=0)


if __name__ == '__main__':
    unittest.main()
