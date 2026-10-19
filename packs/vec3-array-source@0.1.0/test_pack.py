"""Exercise producer contracts and consume their files with compose-video's parser."""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

import run as source

PACK = Path(__file__).resolve().parent


class SourceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ref = self.root / "ref"
        self.ref.mkdir()
        for name in ["custom_B", "custom_A"]:
            (self.ref / name).mkdir()
        (self.ref / ".metadata").write_text("ignored")
        self.value = [0.7, 0.02, 0.25] if source.VECTOR else 2.5

    def run_source(self, items, ref=True):
        out = self.root / "out"
        source.run(
            {
                "inputs": {"videos": str(self.ref)} if ref else {},
                "params": {"items": items if isinstance(items, str) else json.dumps(items)},
                "outputs": {source.PORT: str(out)},
            }
        )
        return out

    def test_reference_assignment_and_consumer(self):
        out = self.run_source([{source.FIELD: self.value}, {source.FIELD: self.value}])
        self.assertEqual(sorted(p.name for p in out.iterdir()), ["custom_A", "custom_B"])
        consumer = PACK.parent / "compose-video@0.1.0"
        sys.path.insert(0, str(consumer))
        self.addCleanup(lambda: sys.path.remove(str(consumer)))
        spec = importlib.util.spec_from_file_location("compose_contract", consumer / "run.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        values = module.attributes(out, ["custom_A", "custom_B"], source.PORT, source.VECTOR)
        self.assertEqual(values, [self.value, self.value])
        with self.assertRaisesRegex(ValueError, "empty"):
            self.run_source([{source.FIELD: self.value}, {source.FIELD: self.value}])

    def test_explicit_ids_without_reference(self):
        out = self.run_source([{"id": "0000", source.FIELD: self.value}], False)
        self.assertEqual(json.loads((out / "0000").read_text()), self.value)

    def test_invalid_items_leave_no_output(self):
        bad = [
            [],
            {},
            [{}],
            [{"id": "custom_A", source.FIELD: self.value}],
            [
                {"id": "custom_A", source.FIELD: self.value},
                {"id": "wrong", source.FIELD: self.value},
            ],
            [{"id": "custom_A", source.FIELD: self.value}, {source.FIELD: self.value}],
            [
                {"id": "custom_A", source.FIELD: self.value},
                {"id": "custom_A", source.FIELD: self.value},
            ],
        ]
        for items in bad:
            with self.subTest(items=items), self.assertRaises(ValueError):
                self.run_source(items)
            self.assertFalse((self.root / "out").exists())
        invalid_values = (
            [
                None,
                True,
                "2",
                {},
                [0, 0],
                [0, 0, 0],
                [0, 0, -1],
                [0, True, 1],
                [0, float("nan"), 1],
                [0, float("inf"), 1],
            ]
            if source.VECTOR
            else [None, True, "2", {}, [], float("nan"), float("inf"), -1]
        )
        for value in invalid_values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.run_source([{"id": "0000", source.FIELD: value}], False)
        for key in ["../escape", ".hidden", "a/b", "a\\b", "bad\nname", ""]:
            with self.subTest(id=key), self.assertRaises(ValueError):
                self.run_source([{"id": key, source.FIELD: self.value}], False)
        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
            self.run_source('[{"id":"a","id":"b","' + source.FIELD + '":0}]', False)
        with self.assertRaisesRegex(ValueError, "require"):
            self.run_source([{source.FIELD: self.value}], False)
        with self.assertRaises(ValueError):
            self.run_source([{"id": "a", source.FIELD: self.value, "typo": 0}], False)


if __name__ == "__main__":
    unittest.main()
