"""Path parameters retain string serialization; only the executing node stats them."""

from pathlib import Path

from hololab.manifest.schema import ParamSpec, ParamType


def resolve_path_params(
    specs: dict[str, ParamSpec], params: dict, *, check_exists: bool = True
) -> dict:
    result = dict(params)
    for name, spec in specs.items():
        if spec.type is ParamType.ARRAY and spec.items is ParamType.PATH:
            value = params.get(name, spec.default)
            spec.validate_array_value(name, value)
            if value is None and spec.optional:
                continue
            element = spec.model_copy(
                update={"type": ParamType.PATH, "items": None, "default": None, "optional": False}
            )
            result[name] = [
                resolve_path_params(
                    {f"{name}[{i}]": element}, {f"{name}[{i}]": item}, check_exists=check_exists
                )[f"{name}[{i}]"]
                for i, item in enumerate(value)
            ]
            continue
        if spec.type is not ParamType.PATH:
            continue
        value = params.get(name, spec.default)
        if value is None and spec.optional:
            continue
        if not isinstance(value, str) or not value.strip() or "\0" in value:
            raise ValueError(f"Parameter {name}: enter a nonempty path string")
        try:
            path = Path(value).expanduser()
        except RuntimeError as exc:
            raise ValueError(f"Parameter {name}: cannot resolve user home directory") from exc
        if not path.is_absolute():
            raise ValueError(f"Parameter {name}: use an absolute path on the compute node")
        try:
            path = path.resolve(strict=False)
            try:
                path.stat()
                exists = True
            except FileNotFoundError:
                exists = False
            if check_exists and spec.must_exist is not False and not exists:
                raise ValueError(
                    f"Parameter {name}: path does not exist on this compute node: {path}"
                )
            if exists and spec.path_kind == "file" and not path.is_file():
                raise ValueError(f"Parameter {name}: expected a file: {path}")
            if exists and spec.path_kind == "directory" and not path.is_dir():
                raise ValueError(f"Parameter {name}: expected a directory: {path}")
        except (OSError, RuntimeError) as exc:
            raise ValueError(f"Parameter {name}: cannot access path: {exc}") from exc
        result[name] = str(path)
    return result
