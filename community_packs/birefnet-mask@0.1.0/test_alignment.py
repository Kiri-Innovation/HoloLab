"""Alignment failures must stop before model loading or a success marker."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

spec = importlib.util.spec_from_file_location('birefnet_pack', Path(__file__).with_name('run.py'))
pack = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pack)


class AlignmentTests(unittest.TestCase):
    def check_rejected(self, count, swapped, message):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ref = root / 'reference'
            ref.mkdir()
            frames = [np.full((4, 6, 3), v, dtype=np.uint8) for v in (20, 180)]
            for i in range(count):
                Image.fromarray(frames[1-i if swapped else i]).save(ref / f'p_{i:016d}.png')
            video = root / 'video.mp4'
            video.touch()

            class Extractor:
                @staticmethod
                def extract_frames(video, output, **kwargs):
                    for i, frame in enumerate(frames):
                        Image.fromarray(frame).save(output / f'p_{i:016d}.png')
                    return len(frames)

            argv = ['run.py', '--video', str(video), '--output', str(root / 'out'),
                    '--scratch', str(root / 'scratch'), '--checkpoint', str(root),
                    '--hololab-root', str(Path(__file__).resolve().parents[2]),
                    '--reference-frames', str(ref)]
            with patch.object(sys, 'argv', argv), patch.object(pack, 'load_module', return_value=Extractor), \
                 patch('transformers.AutoModelForImageSegmentation.from_pretrained') as model:
                with self.assertRaisesRegex(ValueError, message):
                    pack.main()
                model.assert_not_called()
            self.assertFalse((root / 'out/.hololab-done').exists())
            self.assertEqual(list((root / 'out').glob('*.png')), [])

    def test_count_mismatch(self):
        self.check_rejected(1, False, 'count/stem coverage mismatch')

    def test_same_count_swapped_pixels(self):
        self.check_rejected(2, True, 'pixel mismatch')


if __name__ == '__main__':
    unittest.main()
