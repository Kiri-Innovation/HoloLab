"""Path-discovered type definitions. No data validation policy lives here."""
from __future__ import annotations

import hashlib
import importlib
import json
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

BUNDLED_ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class TypeDefinition:
    name: str
    directory: Path
    declaration: dict[str, Any]

    def call(self, hook: str, *args, **kwargs):
        target = self.declaration.get(hook)
        if not target:
            return None
        module, function = target.split(":", 1)
        if module.endswith(".py"):
            path = self.directory / module
            namespace = {"__file__": str(path), "__name__": "_hololab_type"}
            exec(compile(path.read_bytes(), str(path), "exec"), namespace)
            return namespace[function](*args, **kwargs)
        return getattr(importlib.import_module(module), function)(*args, **kwargs)

    def summary(self, path: Path, storage: str) -> dict | None:
        if not self.declaration.get("summary"):
            return None
        try:
            result = subprocess.run(
                [sys.executable, "-m", "hololab.type_worker"],
                input=json.dumps({"directory": str(self.directory), "declaration": self.declaration,
                                  "path": str(path), "storage": storage}),
                capture_output=True, text=True, timeout=2,
            )
            value = json.loads(result.stdout) if result.returncode == 0 else None
            return value if isinstance(value, dict) else None
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return None


class TypeRegistry:
    def __init__(self, roots=(), *, bundled=True):
        self.roots = tuple(Path(p) for p in roots)
        self.bundled = bundled
        self.definitions: dict[str, TypeDefinition] = {}
        self.generation = ""
        self._lock = threading.RLock()
        self.reload()

    def reload(self):
        definitions = {}
        digest = hashlib.sha256()
        roots = (*self.roots, *((BUNDLED_ROOT,) if self.bundled else ()))
        for root in roots:
            if root.is_file():
                root = root.parent
            for manifest in sorted((root / "types").glob("*/manifest.yaml")):
                try:
                    # Include implementations/assets, including edits with unchanged size/mtime.
                    for file in sorted(manifest.parent.rglob("*")):
                        if file.is_file() and "__pycache__" not in file.parts:
                            digest.update(str(file).encode())
                            digest.update(file.read_bytes())
                    data = yaml.safe_load(manifest.read_text())
                    entries = data.get("types", []) if data.get("kind") == "TypeSet" else [data]
                    for entry in entries:
                        if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                            definitions.setdefault(entry["name"], TypeDefinition(entry["name"], manifest.parent, entry))
                except (OSError, ValueError, AttributeError, yaml.YAMLError):
                    continue
        with self._lock:
            self.definitions = definitions
            self.generation = digest.hexdigest()
        return self.generation

    def get(self, name):
        return self.definitions.get(name)


registry = TypeRegistry()


def configure(roots):
    registry.roots = tuple(Path(p) for p in roots)
    registry.reload()
