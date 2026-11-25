"""Bind one manifest to Python values inside the pack's interpreter."""

from __future__ import annotations

import importlib.util
import inspect
import json
import sys
import tempfile
from collections.abc import Mapping
from contextlib import ExitStack
from pathlib import Path
from types import MappingProxyType
from uuid import uuid4

if __name__ == "__main__" and not __package__:
    # The worker ships with the node. Its interpreter and third-party dependencies
    # come from the algorithm environment, including in a source checkout.
    sys.path[0] = str(Path(__file__).resolve().parents[1])

from hololab.locked_codecs import locked_codecs
from hololab.manifest.path_params import resolve_path_params
from hololab.manifest.schema import load_manifest
from hololab.typed_values import HType, RunContext, codec


def effective_params(specs, supplied):
    unknown = supplied.keys() - specs.keys()
    if unknown:
        raise ValueError(f"unknown parameters: {sorted(unknown)}")
    result = {}
    for name, spec in specs.items():
        value = supplied.get(name, spec.default)
        if value is None:
            if not spec.optional:
                raise ValueError(f"missing parameter: {name}")
            continue
        spec.validate_scalar_value(name, value)
        spec.validate_enum_value(name, value)
        spec.validate_array_value(name, value)
        expected = {"bool": bool, "string": str, "path": str, "list[str]": list}
        kind = spec.type.value
        if kind in expected and not isinstance(value, expected[kind]):
            raise ValueError(f"{name}: expected {kind}")
        if kind == "list[str]" and not all(isinstance(item, str) for item in value):
            raise ValueError(f"{name}: expected strings")
        result[name] = value
    return MappingProxyType(resolve_path_params(specs, result))


def bind_value(value, declaration, ctx, override=None):
    matches = []
    failures = []
    for spec in getattr(declaration, "any_of", []) or [declaration]:
        expected = override or codec(spec)
        try:
            item = expected.read(value, spec=spec, io=ctx) if isinstance(value, Path) else value
            if not isinstance(item, expected):
                raise ValueError(f"expected {expected.__name__}")
            item.validate(spec=spec)
            matches.append(item)
        except (ValueError, OSError) as exc:
            failures.append(str(exc))
    if len(matches) != 1:
        raise ValueError(f"expected one matching type, found {len(matches)}: {failures}")
    return matches[0]


class Invocation:
    def __init__(self, manifest_path):
        self.manifest, _ = load_manifest(manifest_path)
        entry = self.manifest.exec.typed
        if not entry:
            raise ValueError("expected exec.typed declaration")
        self.custom_codecs = entry.custom_codecs
        self.manifest_path = manifest_path.resolve()
        self.type_packs = entry.type_packs
        filename, self.function = entry.entry.split(":")
        self.adapter = (manifest_path.parent / filename).resolve()
        if self.adapter.parent != manifest_path.parent.resolve() or not self.adapter.is_file():
            raise ValueError("typed entry must be a file in the manifest directory")

    def invoke(self, inputs, params, ctx):
        with locked_codecs(self.manifest_path, self.type_packs) as codecs:
            return self._invoke(inputs, params, ctx, codecs)

    def _invoke(self, inputs, params, ctx, locked):
        specs = self.manifest.inputs
        if inputs.keys() - specs.keys():
            raise ValueError("unknown input port")
        kwargs = self._bind_inputs(inputs, params, ctx, locked) if not self.custom_codecs else None
        module_spec = importlib.util.spec_from_file_location(
            f"_hololab_{uuid4().hex}", self.adapter
        )
        module = importlib.util.module_from_spec(module_spec)
        sys.modules[module_spec.name] = module
        previous_path = sys.path.copy()
        try:
            sys.path.insert(0, str(self.adapter.parent))
            module_spec.loader.exec_module(module)
            overrides = module.CODECS if self.custom_codecs else {}
            ports = specs.keys() | self.manifest.outputs.keys()
            if (
                not isinstance(overrides, Mapping)
                or overrides.keys() - ports
                or any(
                    not inspect.isclass(kind)
                    or not issubclass(kind, HType)
                    or getattr(self.manifest.inputs.get(name), "any_of", ())
                    for name, kind in overrides.items()
                )
            ):
                raise ValueError("CODECS must map declared non-union ports to HType classes")
            if overrides.keys() & locked.keys():
                raise ValueError("pack-local CODECS conflict with locked Type Pack ports")
            overrides = {**locked, **overrides}
            if kwargs is None:
                kwargs = self._bind_inputs(inputs, params, ctx, overrides)
            fn = getattr(module, self.function)
            signature = inspect.signature(fn)
            if signature.parameters.keys() != kwargs.keys() or any(
                p.kind != p.KEYWORD_ONLY for p in signature.parameters.values()
            ):
                raise ValueError(f"invalid typed signature for {self.function}{signature}")
            result = fn(**kwargs)
        finally:
            sys.path[:] = previous_path
            sys.modules.pop(module_spec.name, None)
        outputs = self.manifest.outputs
        if len(outputs) == 1 and isinstance(result, HType):
            result = {next(iter(outputs)): result}
        if not isinstance(result, Mapping) or result.keys() != outputs.keys():
            raise ValueError("return keys must match output ports")
        for name, spec in outputs.items():
            if not isinstance(result[name], HType):
                raise ValueError(f"{name}: expected typed value")
            bind_value(result[name], spec, ctx, overrides.get(name))
        return result

    def _bind_inputs(self, inputs, params, ctx, overrides):
        specs = self.manifest.inputs
        kwargs = {}
        for name, spec in specs.items():
            if name not in inputs and not spec.required:
                kwargs[name] = None
                continue
            if name not in inputs:
                raise ValueError(f"missing input: {name}")
            kwargs[name] = bind_value(inputs[name], spec, ctx, overrides.get(name))
        if {"params", "ctx"} & kwargs.keys():
            raise ValueError("typed input collides with reserved parameter")
        kwargs.update(params=effective_params(self.manifest.params, params), ctx=ctx)
        return kwargs


def run(manifest_path, request):
    call = Invocation(manifest_path)
    with locked_codecs(call.manifest_path, call.type_packs) as codecs:
        _run(call, manifest_path, request, codecs)


def _run(call, manifest_path, request, codecs):
    ctx = RunContext(Path(request["scratch"]).resolve())
    inputs = {k: Path(p).resolve() for k, p in request["inputs"].items()}
    outputs = {k: Path(p).absolute() for k, p in request["outputs"].items()}
    if outputs.keys() != call.manifest.outputs.keys():
        raise ValueError("output destinations must match declared ports")
    pack_dir = manifest_path.parent.resolve()
    protected = [*inputs.values(), pack_dir, (pack_dir / call.manifest.root).resolve()]
    for path in [ctx.scratch, *outputs.values()]:
        resolved = path.resolve()
        if any(resolved == p or resolved in p.parents or p in resolved.parents for p in protected):
            raise ValueError(f"overlapping worker paths: {path}")
        protected.append(resolved)
    for name, path in outputs.items():
        precreated = call.manifest.outputs[name].storage.value == "dir" and path.is_dir()
        if path.is_symlink() or (path.exists() and (not precreated or any(path.iterdir()))):
            raise ValueError(f"output already exists: {path}")
    ctx.scratch.mkdir(exist_ok=True, parents=True)
    result = call._invoke(inputs, request["params"], ctx, codecs)
    with ExitStack() as stack:
        staged = {}
        for name, target in outputs.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = stack.enter_context(tempfile.TemporaryDirectory(dir=target.parent))
            staged[name] = Path(tmp) / "value"
            spec = call.manifest.outputs[name]
            result[name].write(staged[name], spec=spec, io=ctx)
            if staged[name].is_dir():
                (staged[name] / ".hololab-done").touch()
        # The runtime registers handles only after a successful exit. Undo prior
        # renames if publishing a later port fails, so the job is retryable.
        with ExitStack() as rollback:
            for name, path in staged.items():
                target = outputs[name]
                if target.exists():
                    rollback.callback(target.mkdir, exist_ok=True)
                path.replace(target)
                rollback.callback(target.replace, path)
            rollback.pop_all()


if __name__ == "__main__":
    run(Path(sys.argv[1]).resolve(), json.load(sys.stdin))
