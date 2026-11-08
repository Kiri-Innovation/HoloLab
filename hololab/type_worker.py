"""Disposable summary process; parent enforces the two-second deadline."""
import contextlib
import json
import sys
from pathlib import Path

from hololab.type_registry import TypeDefinition


def main():
    request = json.load(sys.stdin)
    sys.path.insert(0, request["directory"])
    definition = TypeDefinition("", Path(request["directory"]), request["declaration"])
    # A type's diagnostic print must never corrupt the JSON protocol.
    with contextlib.redirect_stdout(sys.stderr):
        result = definition.call("summary", Path(request["path"]), request["storage"])
    json.dump(result, sys.stdout)


if __name__ == "__main__":
    main()
