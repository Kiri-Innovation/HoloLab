"""Disposable summary process; parent enforces the two-second deadline."""

import contextlib
import importlib.machinery
import json
import sys
from pathlib import Path

from hololab.type_registry import TypeDefinition


class SourceOnlyLoader(importlib.machinery.SourceFileLoader):
    def get_code(self, fullname):
        return self.source_to_code(self.get_data(self.path), self.path)


class TypeImportFinder:
    """Type-local helpers always read source, even within one pyc timestamp tick."""

    def __init__(self, directory):
        self.directory = Path(directory).resolve()

    def find_spec(self, fullname, path=None, target=None):
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec and spec.origin and spec.origin.endswith(".py"):
            origin = Path(spec.origin).resolve()
            if origin.is_relative_to(self.directory):
                spec.loader = SourceOnlyLoader(fullname, str(origin))
                return spec
        return None


def main():
    request = json.load(sys.stdin)
    sys.path.insert(0, request["directory"])
    sys.meta_path.insert(0, TypeImportFinder(request["directory"]))
    definition = TypeDefinition("", Path(request["directory"]), request["declaration"])
    # A type's diagnostic print must never corrupt the JSON protocol.
    with contextlib.redirect_stdout(sys.stderr):
        result = definition.call("summary", Path(request["path"]), request["storage"])
    json.dump(result, sys.stdout)


if __name__ == "__main__":
    main()
