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
# ``OutputPreview.viewer`` (splatv / video / image / text / video-grid).
TAG_VIEWER_REGISTRY: dict[str, OutputPreview] = {
    # Video source — one video file in a directory (scalar). Also the
    # element type for arrayed<video-source> from video-array-source; the
    # video-grid viewer paints one tile (per its default filter) either way.
    # The M7 arrayed migration retired the standalone ``video-source-array``
    # tag; cardinality is expressed via ``PortSpec.arrayed`` now.
    "video-source": OutputPreview(viewer="video-grid"),
    # STG-to-Splatv output — the interactive Gaussian splat renderer
    # already keys on the ``splatv`` tag, so packs that produce it
    # don't need to repeat themselves.
    "splatv": OutputPreview(viewer="splatv"),
    # Ordered frame sequence (``frames/frame_XXXXXX.png``) — the abstract
    # object shared between the frame extractor and any consumer that
    # wants a sequence of images (MegaSaM tracker, future re-runs of
    # other trackers). The default viewer just shows the first frame;
    # promoting it to a proper image-grid viewer is a follow-up.
    # ``image_sequence`` is the preferred new name; ``frame_sequence`` is
    # kept as an alias entry so pre-migration handles still preview through
    # the same viewer. Edge-compat unification lives in workflows.py:TAG_ALIASES.
    "image_sequence": OutputPreview(viewer="image", member="frames/frame_000000.png"),
    "frame_sequence": OutputPreview(viewer="image", member="frames/frame_000000.png"),
    # Scalar ``int`` handle — a small plain-text file whose sole content
    # is a base-10 integer. The text viewer shows the value verbatim; a
    # dedicated big-number scalar viewer is a follow-up when the UX
    # needs it.
    "int": OutputPreview(viewer="text"),
}


def infer_preview_for_output(
    explicit_preview: OutputPreview | None,
    tags: list[str],
) -> OutputPreview | None:
    """Return the preview spec for one output port.

    Explicit manifest declaration wins; otherwise the first tag with a
    registry entry supplies the default. Returns ``None`` when nothing
    matches — that port has no preview drawer.
    """

    if explicit_preview is not None:
        return explicit_preview
    for tag in tags:
        registered = TAG_VIEWER_REGISTRY.get(tag)
        if registered is not None:
            return registered
    return None
