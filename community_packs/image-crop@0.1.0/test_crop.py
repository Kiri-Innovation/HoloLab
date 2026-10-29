"""Run: python -m unittest discover -s packs/image-crop@0.1.0 -v"""

import contextlib
import io
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

import cv2
import numpy as np
from crop import crop_image, pixel_padding
from PIL import Image


class CropTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.src, self.out = self.root / "input.png", self.root / "output"
        self.a = np.arange(12 * 16 * 3, dtype=np.uint8).reshape(12, 16, 3)
        Image.fromarray(self.a).save(self.src)

    def run_crop(self, pads, color="#000000"):
        with contextlib.redirect_stdout(io.StringIO()):
            crop_image(self.src, self.out, pads, color)
        with Image.open(self.out) as im:
            return np.array(im)

    def test_asymmetric_and_mixed_coordinates(self):
        np.testing.assert_array_equal(self.run_crop((2, 3, 5, 1)), self.a[3:11, 2:11])
        b = self.run_crop((2, -3, 5, -1), "#204060")
        self.assertEqual(b.shape, (16, 9, 3))
        np.testing.assert_array_equal(b[3:15], self.a[:, 2:11])
        np.testing.assert_array_equal(b[0, 0], [32, 64, 96])

    def test_zero_is_byte_identical(self):
        self.run_crop((0, 0, 0, 0))
        self.assertEqual(self.src.read_bytes(), self.out.read_bytes())

    def test_bounds_and_rounding(self):
        self.assertEqual([pixel_padding(x) for x in (0.49, 0.5, -0.5, 1.5)], [0, 1, -1, 2])
        self.assertEqual(self.run_crop((15, 11, 0, 0)).shape, (1, 1, 3))
        for pads in [
            (16, 0, 0, 0),
            (10, 0, 6, 0),
            (17, 0, -30, 0),
            (0, 12, 0, 0),
            (float("nan"), 0, 0, 0),
            (-1e9, 0, 0, 0),
        ]:
            with self.subTest(pads=pads), self.assertRaises(ValueError):
                self.run_crop(pads)
        with self.assertRaisesRegex(ValueError, "#RRGGBB"):
            self.run_crop((0, 0, 0, 0), "red")

    def test_formats_and_extensionless_input(self):
        for ext, fmt in [("png", "PNG"), ("jpg", "JPEG"), ("jpeg", "JPEG"), ("webp", "WEBP")]:
            with self.subTest(ext=ext):
                path = self.root / ("original." + ext)
                Image.fromarray(self.a).save(path)
                self.src = self.root / "extensionless"
                self.src.write_bytes(path.read_bytes())
                self.run_crop((2, 3, 5, 1))
                with Image.open(self.out) as im:
                    self.assertEqual((im.format, im.size), (fmt, (9, 8)))

    def test_png16_grayscale_rgb_rgba(self):
        for channels in (1, 3, 4):
            with self.subTest(channels=channels):
                shape = (12, 16) if channels == 1 else (12, 16, channels)
                a = (np.arange(np.prod(shape), dtype=np.uint16) * 101).reshape(shape)
                cv2.imwrite(str(self.src), a)
                self.run_crop((2, -3, 5, -1), "#204060")
                b = cv2.imdecode(
                    np.frombuffer(self.out.read_bytes(), np.uint8), cv2.IMREAD_UNCHANGED
                )
                self.assertEqual(b.dtype, np.uint16)
                np.testing.assert_array_equal(b[3:15], a[:, 2:11])
                self.assertEqual(self.out.read_bytes()[24], 16)
                if channels > 1:
                    self.assertEqual(
                        b[0, 0].tolist(),
                        [96 * 257, 64 * 257, 32 * 257] + ([65535] if channels == 4 else []),
                    )

    def test_palette_depth_alpha_and_bilevel(self):
        for bits in (1, 2, 4, 8):
            im = Image.new("P", (16, 12), 1)
            im.putpalette([0, 0, 0, 255, 0, 0] + [0] * 762)
            im.save(self.src, bits=bits, transparency=1)
            self.run_crop((-2, 3, 5, 1))
            self.assertEqual(self.out.read_bytes()[24], bits)
            with Image.open(self.out) as out:
                self.assertEqual(out.convert("RGBA").getpixel((2, 0)), (255, 0, 0, 0))
            with self.assertRaisesRegex(ValueError, "palette"):
                self.run_crop((-2, 0, 0, 0), "#123456")
        im.info["transparency"] = 0
        im.save(self.src, bits=1)
        with self.assertRaisesRegex(ValueError, "opaque"):
            self.run_crop((-2, 0, 0, 0))
        Image.new("1", (16, 12), 1).save(self.src)
        self.run_crop((2, 3, 5, 1))
        self.assertEqual(self.out.read_bytes()[24], 1)
        Image.new("RGBA", (16, 12), (1, 2, 3, 4)).save(self.src)
        b = self.run_crop((-2, 0, 0, 0), "#204060")
        self.assertEqual(b[0, 0].tolist(), [32, 64, 96, 255])
        self.assertEqual(b[0, 2].tolist(), [1, 2, 3, 4])

    def test_low_bit_grayscale(self):
        def chunk(kind, data):
            return (
                struct.pack(">I", len(data))
                + kind
                + data
                + struct.pack(">I", zlib.crc32(kind + data))
            )

        for depth, samples in [
            (2, bytes([0x1B] * 4)),
            (4, bytes([0x01, 0x23, 0x45, 0x67, 0x89, 0xAB, 0xCD, 0xEF])),
        ]:
            self.src.write_bytes(
                b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", 16, 12, depth, 0, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress((b"\0" + samples) * 12))
                + chunk(b"IEND", b"")
            )
            with Image.open(self.src) as im:
                expected = np.array(im)[3:11, 2:11]
            self.run_crop((2, 3, 5, 1))
            self.assertEqual(self.out.read_bytes()[24], depth)
            with Image.open(self.out) as im:
                np.testing.assert_array_equal(np.array(im.convert("L")), expected)


if __name__ == "__main__":
    unittest.main()
