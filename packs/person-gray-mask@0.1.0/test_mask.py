import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

s = importlib.util.spec_from_file_location("person_mask", Path(__file__).with_name("mask.py"))
m = importlib.util.module_from_spec(s)
s.loader.exec_module(m)


class MasksTest(unittest.TestCase):
    def test_subject_and_arm_gap(self):
        rgb = np.full((300, 300, 3), (120, 120, 115), np.uint8)
        cv2.rectangle(rgb, (110, 90), (190, 299), (205, 160, 145), -1)
        cv2.circle(rgb, (150, 70), 40, (20, 15, 15), -1)
        cv2.rectangle(rgb, (120, 230), (180, 299), (230, 230, 230), -1)
        cv2.rectangle(rgb, (60, 120), (110, 200), (205, 160, 145), 10)
        mask, _ = m.segment(rgb)
        self.assertEqual(mask[70, 150], 255)
        self.assertEqual(mask[260, 150], 255)
        self.assertEqual(mask[160, 80], 0)
        self.assertEqual(mask[20, 20], 0)
        self.assertEqual(mask.shape, rgb.shape[:2])

    def test_dark_wall_shadow_is_not_subject(self):
        rgb = np.full((300, 300, 3), (120, 120, 115), np.uint8)
        rgb[180:] = (93, 87, 82)
        cv2.rectangle(rgb, (110, 90), (190, 299), (205, 160, 145), -1)
        cv2.circle(rgb, (150, 70), 40, (20, 15, 15), -1)
        mask, _ = m.segment(rgb)
        self.assertEqual(mask[280, 20], 0)
        self.assertEqual(mask[20, 20], 0)
        self.assertEqual(mask[280, 150], 255)

    def test_duplicate_stems_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / "images").mkdir()
            for ext in ["png", "jpg"]:
                (root / "images" / ("frame." + ext)).touch()
            with (
                patch.object(
                    sys,
                    "argv",
                    ["mask.py", "--colmap", t, "--output", t + "/out", "--report", t + "/report"],
                ),
                self.assertRaisesRegex(ValueError, "duplicate stems"),
            ):
                m.main()


if __name__ == "__main__":
    unittest.main()
