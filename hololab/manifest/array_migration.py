"""Pack-scoped adapters for the initial array sources; never rewrite stored graphs."""

import json


def migrate_array_params(name: str, version: str, params: dict) -> dict:
    if version != "0.1.0":
        return params
    result = dict(params)
    if (
        name == "video-array-assemble"
        and ("paths" not in result or result["paths"] == [])
        and "paths_json" in result
    ):
        raw = result.pop("paths_json")
        result["paths"] = json.loads(raw) if isinstance(raw, str) else raw
    if name in ("float-array-source", "vec3-array-source") and isinstance(result.get("items"), str):

        def unique(pairs):
            obj = {}
            for key, value in pairs:
                if key in obj:
                    raise ValueError(f"duplicate JSON key: {key}")
                obj[key] = value
            return obj

        rows = json.loads(result["items"], object_pairs_hook=unique)
        field = "offset" if name == "float-array-source" else "transform"
        if (
            not isinstance(rows, list)
            or not rows
            or any(
                not isinstance(row, dict) or field not in row or set(row) - {"id", field}
                for row in rows
            )
        ):
            raise ValueError("legacy items must be nonempty objects with value and optional id")
        explicit = ["id" in row for row in rows]
        if any(explicit) and not all(explicit):
            raise ValueError("items must either all specify id or all omit it")
        result["ids"] = [row["id"] for row in rows] if all(explicit) else []
        if field == "offset":
            result["items"] = [row[field] for row in rows]
        else:
            vectors = [row[field] for row in rows]
            if any(not isinstance(v, list) or len(v) != 3 for v in vectors):
                raise ValueError("legacy transform must contain exactly three numbers")
            result.pop("items")
            for i, key in enumerate(("x", "y", "scale")):
                result[key] = [v[i] for v in vectors]
    return result
