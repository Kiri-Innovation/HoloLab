"""Algorithm pack manifest — parsing, validation, and template rendering.

See docs/pack-spec.md for the schema.
"""

from hololab.manifest.render import (
    RenderContext,
    render_manifest,
    rendered_output_paths,
    rendered_scratch_dir,
    rendered_staging_dir,
)
from hololab.manifest.schema import (
    ExecSpec,
    IdempotencySpec,
    InputSpec,
    Manifest,
    OutputPreview,
    OutputSpec,
    ParamSpec,
    ParamType,
    PreviewSpec,
    ProgressSpec,
    RuntimeSpec,
    StorageForm,
    load_manifest,
)

__all__ = [
    "ExecSpec",
    "IdempotencySpec",
    "InputSpec",
    "Manifest",
    "OutputPreview",
    "OutputSpec",
    "ParamSpec",
    "ParamType",
    "PreviewSpec",
    "ProgressSpec",
    "RenderContext",
    "RuntimeSpec",
    "StorageForm",
    "load_manifest",
    "render_manifest",
    "rendered_output_paths",
    "rendered_scratch_dir",
    "rendered_staging_dir",
]
