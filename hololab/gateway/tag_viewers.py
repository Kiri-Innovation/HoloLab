"""Tag → preview-viewer registry (default preview inference from tags).

Rationale
---------

Historically each pack that wanted a preview drawer had to declare
``preview: {viewer: …, member?: …}`` on the relevant output port. That
worked but pushed a viewer decision into every pack manifest — a
``splatv`` output on any producer, past or future, has to know it
should route to the ``splatv`` viewer; a ``video-source`` output has
to know about ``video-grid``. Same viewer choice repeated on every
producing pack.

Under the tag-driven model, a **tag** names a data class (e.g.
``video-source``, ``video-source-array``, ``splatv``). A single
registry maps that tag
to the canonical viewer for the class. Any pack whose output port
carries that tag automatically gets the viewer — no manifest change
needed. New viewers or new tags are one entry in this dict, plus a
component on the frontend for the viewer kind if it's genuinely new.

Precedence
----------

If a pack manifest still declares an explicit ``preview:`` block on
an output port, the explicit declaration WINS — the registry is only
consulted when the manifest is silent. This gives pack authors an
escape hatch when the default inference isn't right (e.g. a pack that
produces ``splatv`` data but wants the metadata card viewer instead of
the interactive splatv renderer, or a pack whose tag choice for other
reasons shouldn't drive preview).
"""

from __future__ import annotations

from hololab.manifest.schema import OutputPreview

# Tag → default preview declaration. Extend this dict — plus a frontend
# viewer component if the ``viewer`` value is new — to add a new
# tag-driven preview family.
#
# Naming: keys are the exact tag strings on manifest output ports; the
# ``viewer`` field must be one of the literals declared on
# ``OutputPreview.viewer`` (splatv / ftgsply / ply-3dgs / lagsplat / video / image / text / video-grid).
TAG_VIEWER_REGISTRY: dict[str, OutputPreview] = {
    "colmap4d-cams": OutputPreview(viewer="colmap4d", member="."),
    "colmap4d": OutputPreview(viewer="colmap4d", member="sparse/0"),
    # Video source — one video file in a directory (scalar). Also the
    # element type for arrayed<video-source> from builtin/video-array-source; the
    # video-grid viewer paints one tile (per its default filter) either way.
    # The M7 arrayed migration retired the standalone ``video-source-array``
    # tag; cardinality is expressed via ``PortSpec.arrayed`` now.
    "video-source": OutputPreview(viewer="video-grid"),
    # STG-to-Splatv output — the interactive Gaussian splat renderer
    # already keys on the ``splatv`` tag, so packs that produce it
    # don't need to repeat themselves.
    "splatv": OutputPreview(viewer="splatv"),
    "ply-3dgs": OutputPreview(viewer="ply-3dgs"),
    "ftgsply": OutputPreview(viewer="ftgsply"),
    # LaGSplat checkpoint directory exported by lagsplat-train.  The model
    # tag, rather than the generic implementation-state tag, makes this a
    # portable data-class contract for any future producer.
    "lagsplat_model": OutputPreview(viewer="lagsplat"),
    # A single image file, not a directory of frames.
    "image": OutputPreview(viewer="image"),
    # Scalar ``int`` handle — a small plain-text file whose sole content
    # is a base-10 integer. The text viewer shows the value verbatim; a
    # dedicated big-number scalar viewer is a follow-up when the UX
    # needs it.
    "int": OutputPreview(viewer="text"),
}


def infer_preview_for_output(
    explicit_preview: OutputPreview | None,
    tags: list[str],
    storage: str | None = None,
) -> OutputPreview | None:
    """Resolve declaration-owned defaults and historical preview precedence."""
    from hololab.type_registry import registry

    def declared_preview(value, definition):
        from urllib.parse import quote
        result = OutputPreview(**value)
        if result.entry:
            result = result.model_copy(update={"entry": "/api/types/" + quote(definition.name, safe="") + "/assets/" + result.entry + "?v=" + registry.generation})
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
        if definition.declaration.get("preview_terminal") and preview and preview.viewer == value.get("viewer"):
            options = {}
            for matched in definitions:
                options.update(matched.declaration.get("preview", {}).get("options", {}))
            return preview.model_copy(update={"options": {**options, **preview.options}})
    rules = sorted(((d.declaration["preview_rule"], d) for d in definitions if "preview_rule" in d.declaration), key=lambda item: item[0]["order"])
    for rule, definition in rules:
        if storage is not None and rule.get("storage") not in (None, storage):
            continue
        override = rule.get("override", "missing")
        if preview is not None and override != "any" and not (override == "text" and preview.viewer == "text"):
            continue
        return declared_preview(rule["preview"], definition)
    return preview
