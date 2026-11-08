"""Resolve type-owned preview declarations for catalog and handle responses."""

from __future__ import annotations

from hololab.manifest.schema import OutputPreview


def infer_preview_for_output(
    explicit_preview: OutputPreview | None,
    tags: list[str],
    storage: str | None = None,
    arrayed: bool | None = None,
) -> OutputPreview | None:
    """Resolve declaration-owned defaults and historical preview precedence."""
    from hololab.type_registry import registry

    def declared_preview(value, definition):
        from urllib.parse import quote

        result = OutputPreview(**value)
        if result.entry:
            result = result.model_copy(
                update={
                    "entry": "/api/types/"
                    + quote(definition.name, safe="")
                    + "/assets/"
                    + result.entry
                    + "?v="
                    + registry.generation
                }
            )
        return result

    definitions = [d for tag in tags if (d := registry.get(tag)) is not None]
    preview = explicit_preview
    if preview is None:
        for definition in definitions:
            value = definition.declaration.get("preview")
            if value:
                preview = declared_preview(value, definition)
                break
    # Terminal renderer behavior used to precede every browser intercept.
    registry.refresh()
    for definition in registry.definitions.values():
        value = definition.declaration.get("preview", {})
        if (
            definition.declaration.get("preview_terminal")
            and preview
            and preview.viewer == value.get("viewer")
        ):
            options = {}
            for matched in definitions:
                options.update(matched.declaration.get("preview", {}).get("options", {}))
            return preview.model_copy(update={"options": {**options, **preview.options}})
    rules = sorted(
        (
            (d.declaration["preview_rule"], d)
            for d in definitions
            if "preview_rule" in d.declaration
        ),
        key=lambda item: item[0]["order"],
    )
    for rule, definition in rules:
        if storage is not None and rule.get("storage") not in (None, storage):
            continue
        if rule.get("when") and not any(
            all(
                actual is None or condition.get(key, actual) == actual
                for key, actual in (("storage", storage), ("arrayed", arrayed))
            )
            for condition in rule["when"]
        ):
            continue
        override = rule.get("override", "missing")
        if (
            preview is not None
            and override != "any"
            and not (override == "text" and preview.viewer == "text")
        ):
            continue
        return declared_preview(rule["preview"], definition)
    if preview is None:
        for definition in definitions:
            if value := definition.declaration.get("preview_fallback"):
                return declared_preview(value, definition)
    return preview
