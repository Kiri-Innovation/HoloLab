"""Pure initialization, strict values and positional consumer contract."""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

import run as source


class SourceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def produce(self, values):
        params = (
            {
                "x": [v[0] for v in values],
                "y": [v[1] for v in values],
                "scale": [v[2] for v in values],
            }
            if source.VECTOR
            else {"items": values}
        )
        source.run({"params": params, "outputs": {source.PORT: str(self.root / "out")}})
        return self.root / "out"

    def test_native_arrays_and_different_consumer_ids(self):
        values = [[0, 0, 1], [0.7, 0.02, 0.25]] if source.VECTOR else [0, 2.5]
        out = self.produce(values)
        self.assertEqual(sorted(p.name for p in out.iterdir()), ["0000", "0001"])
        consumer = Path(__file__).resolve().parent.parent / "compose-video@0.1.0"
        sys.path.insert(0, str(consumer))
        self.addCleanup(lambda: sys.path.remove(str(consumer)))
        spec = importlib.util.spec_from_file_location("consumer", consumer / "run.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(
            module.attributes(out, ["a-video", "z-video"], source.PORT, source.VECTOR), values
        )
        with self.assertRaisesRegex(ValueError, "has 2 layers; videos has 3"):
            module.attributes(out, ["a", "b", "c"], source.PORT, source.VECTOR)
        with self.assertRaisesRegex(ValueError, "empty"):
            self.produce(values)

    def test_invalid_values_do_not_publish(self):
        bad = (
            [[0, 0, 0], [0, True, 1], [0, float("nan"), 1], [0, 0, float("inf")]]
            if source.VECTOR
            else [True, "2", -1, float("nan"), float("inf")]
        )
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.produce([value])
            self.assertFalse((self.root / "out").exists())
        with self.assertRaises(ValueError):
            self.produce([])

    def test_no_input_or_ids_declaration(self):
        import yaml

        manifest = yaml.safe_load(Path(__file__).with_name("manifest.yaml").read_text())
        self.assertEqual(manifest["inputs"], {})
        self.assertNotIn("ids", manifest["params"])
        if source.VECTOR:
            with self.assertRaisesRegex(ValueError, "matching lengths"):
                source.run({"params": {"x": [0], "y": [], "scale": [1]}})
        with self.assertRaisesRegex(ValueError, "IDs"):
            source.run(
                {
                    "params": {
                        "items": json.dumps(
                            [{"id": "a", source.FIELD: [0, 0, 1] if source.VECTOR else 0}]
                        )
                    }
                }
            )


if __name__ == "__main__":
    unittest.main()
