"""Pack manifest schema — the source of truth for pack validation.

The models here mirror ``docs/pack-spec.md``. Changes to either must land
together.
"""

from __future__ import annotations

import hashlib
from enum import Enum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

API_VERSION = "hololab.dev/v1"


class ParamType(str, Enum):
    """Closed set of parameter types (scalars only; ports are typed by tag)."""

    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    STRING = "string"
    ENUM = "enum"


class StorageForm(str, Enum):
    """How an output/input is physically stored on disk.

    This is an internal transport hint — it drives whether cross-node
    materialization pulls a single file or a tarball of a directory tree.
    Users authoring a graph never see it; it does NOT participate in edge
    compatibility (that's tags-only).
    """

    DIR = "dir"
    FILE = "file"


# ---------------------------------------------------------------------------
# Ports and params
# ---------------------------------------------------------------------------


class InputSpec(BaseModel):
    """Declared input port on an algorithm node.

    ``tags`` is the *object type* the port accepts — the only thing edge
    compatibility looks at. ``storage`` is an internal transport hint (see
    :class:`StorageForm`); it defaults to ``dir`` and never affects edge
    matching. ``description`` is authorial help shown in tooltips.

    ``arrayed`` marks the port as accepting an ``arrayed<T>`` value — a
    directory whose immediate subdirectories are elements. See
    ``docs/pack-spec.md#arrayed-and-arrayable``. Combines with the
    containing pack's ``arrayable`` flag: an arrayable pack's ports flip
    to arrayed at the graph node's ``arrayed_toggle`` (per-node checkbox).

    The special tag ``any`` is a wildcard — it matches every other tag at
    edge-compatibility time. Utility packs (``arrayfy`` / ``get-index`` /
    ``array-length``) that operate over any element type use this.

    Backward-compatible legacy fields (``type``, ``optional``) are accepted
    but no longer participate in validation — they are recorded and ignored.
    """

    model_config = ConfigDict(extra="ignore")

    tags: list[str] = Field(default_factory=list)
    required: bool = True
    storage: StorageForm = StorageForm.DIR
    description: str | None = None
    arrayed: bool = False

    @model_validator(mode="after")
    def _tags_non_empty(self) -> InputSpec:
        if not self.tags:
            raise ValueError("input port must declare at least one tag (object type)")
        return self


class OutputPreview(BaseModel):
    """How the frontend should render this output when the node is expanded.

    Optional per-output declaration. Absence = the canvas node has no
    expand affordance (the "not all nodes support preview" contract from
    the design). Presence = the node grows a small caret; expanding
    reveals the named viewer with the output handle streamed via the
    gateway's ``/proxy/{node}/...`` route.

    Fields:
        viewer  — one of ``splatv | video | image | text | video-grid``.
                  Frontend chooses the concrete component based on this
                  string.
        member  — for ``storage: dir`` outputs, the relative entry inside
                  the directory. For single-file viewers (video/image/…)
                  this is a literal relative path. For ``video-grid`` it
                  is a **glob** that filters entries in the listing (e.g.
                  ``*.mp4``); when omitted, video-grid falls back to a
                  default set of common video extensions. Ignored when
                  the output is ``storage: file``.
    """

    model_config = ConfigDict(extra="ignore")

    viewer: Literal["splatv", "video", "image", "text", "video-grid"]
    member: str | None = None


class OutputSpec(BaseModel):
    """Declared output port on an algorithm node.

    Same semantics as :class:`InputSpec`: ``tags`` names the object type
    this port produces, ``storage`` is the internal transport hint.
    ``preview`` (optional) opts the port into the in-canvas viewer.

    ``arrayed`` marks the port as producing an ``arrayed<T>`` value; same
    per-node override rule as inputs (see :class:`InputSpec` docstring).

    ``tags_from`` names one of this pack's input ports whose effective
    tags this output should mirror. Used by generic utility packs
    (``arrayfy``, ``get-index``) whose element type is determined by
    what the caller wires in. Cyclic references are rejected at
    validation time.
    """

    model_config = ConfigDict(extra="ignore")

    tags: list[str] = Field(default_factory=list)
    storage: StorageForm = StorageForm.DIR
    description: str | None = None
    preview: OutputPreview | None = None
    arrayed: bool = False
    tags_from: str | None = None

    @model_validator(mode="after")
    def _tags_non_empty(self) -> OutputSpec:
        if not self.tags:
            raise ValueError("output port must declare at least one tag (object type)")
        return self


class ParamSpec(BaseModel):
    """Declared user-facing parameter for an algorithm node.

    Params are scalar knobs (``int``, ``float``, ``bool``, ``string``,
    ``enum``) — completely distinct from ports. They never carry tags.
    """

    model_config = ConfigDict(extra="ignore")

    type: ParamType
    default: Any = None
    description: str | None = None
    min: float | int | None = None
    max: float | int | None = None
    values: list[Any] | None = None  # required for enum
    optional: bool = False

    @model_validator(mode="after")
    def _check_enum(self) -> ParamSpec:
        if self.type is ParamType.ENUM and not self.values:
            raise ValueError("param of type 'enum' must declare 'values'")
        return self


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------


class GpuRequirement(BaseModel):
    model_config = ConfigDict(extra="ignore")

    required: bool = False
    vram_gb_min: float | None = None


class ResourcesSpec(BaseModel):
    """Declared per-job resource footprint used by the node preflight.

    All fields are advisory *minimums* — the node checks the reported
    values against live disk/memory before it acks a job and fails fast
    with a clear message when they can't be met. This is what stops the
    "ran 25 minutes, got SIGKILL'd, lost all work" failure mode we
    actually hit on track-to-gs-sequence @ 161 frames.

    Numbers are per-job (not per-workflow) and refer to *peak* usage
    over the pack's lifetime. Leave a field unset to skip its check;
    ``gpu_mem_gb`` is currently informational only (the gateway already
    matches ``runtime.gpu.vram_gb_min`` at assign time — a live check
    against ``nvidia-smi --query-gpu=memory.free`` is deferred).
    """

    model_config = ConfigDict(extra="ignore")

    scratch_gb: float | None = None
    mem_gb: float | None = None
    gpu_mem_gb: float | None = None


class RuntimeSpec(BaseModel):
    """Runtime requirements and hints."""

    model_config = ConfigDict(extra="ignore")

    env: str  # logical env name; node maps to real prefix
    gpu: GpuRequirement = Field(default_factory=GpuRequirement)
    exclusive: bool = False
    expected_duration_min: list[float] | None = None
    resources: ResourcesSpec = Field(default_factory=ResourcesSpec)


# ---------------------------------------------------------------------------
# Exec, idempotency, previews, progress
# ---------------------------------------------------------------------------


class ExecSpec(BaseModel):
    """The command template."""

    model_config = ConfigDict(extra="ignore")

    shell: str
    working_dir: str | None = None


class IdempotencySpec(BaseModel):
    """Marker convention for skipping re-runs."""

    model_config = ConfigDict(extra="ignore")

    marker: str  # templated path


class PreviewSpec(BaseModel):
    """One preview declaration."""

    model_config = ConfigDict(extra="ignore")

    id: str
    type: Literal["image", "video", "text", "ply", "grid", "log"]
    path: str | None = None
    glob: str | None = None

    @model_validator(mode="after")
    def _check_path_or_glob(self) -> PreviewSpec:
        if bool(self.path) == bool(self.glob):
            raise ValueError("preview must set exactly one of 'path' or 'glob'")
        return self


class ProgressSpec(BaseModel):
    """How the node parses progress from the algorithm's output."""

    model_config = ConfigDict(extra="ignore")

    stdout_regex: str | None = None
    status_file: str | None = None

    @model_validator(mode="after")
    def _check_one(self) -> ProgressSpec:
        if not self.stdout_regex and not self.status_file:
            raise ValueError("progress must set one of 'stdout_regex' or 'status_file'")
        return self


# ---------------------------------------------------------------------------
# The manifest itself
# ---------------------------------------------------------------------------


class Manifest(BaseModel):
    """A parsed and validated ``manifest.yaml``.

    Immutable after construction; use ``load_manifest`` to build one from disk.
    """

    # ``extra="ignore"`` — schema evolution policy:
    #
    # A long-running node process re-scans packs whenever their files
    # change. If we forbade unknown fields the way we used to, a schema
    # bump that added (say) ``docs`` or ``preview`` at the top of a
    # pack would silently reset the online pack count to zero on the
    # next rescan — every previously-parsed manifest would now fail.
    # Ignoring unknowns keeps old nodes usable through a schema bump;
    # ``load_manifest`` still logs the dropped keys so authors notice
    # typos. Same policy is applied to every nested manifest model.
    #
    # Restart-to-see-new-features is still the current reality: the
    # node caches parsed manifests, so a *new* field authored on disk
    # doesn't take effect until the node re-loads. Documented in
    # ``docs/pack-spec.md#schema-evolution``.
    model_config = ConfigDict(extra="ignore", frozen=True)

    apiVersion: str
    kind: Literal["Algorithm"]
    name: str
    version: str
    description: str | None = None
    category: list[str] = Field(default_factory=list)
    docs: str | None = None
    # ⌘/Ctrl+click "Jump to source" target — the pack's core implementation
    # script. A relative path resolves against ``manifest.yaml``'s own
    # directory (self-contained + portable); an absolute path is used
    # verbatim. When absent the modifier+click falls back to opening the
    # pack directory. See docs/cobrowser-integration.md#jump-to-source.
    source_entry: str | None = None
    # Declares this pack's exec is data-parallel over its arrayed inputs.
    # When true, the frontend shows an "arrayed" checkbox on the node; when
    # the operator turns it on, the scheduler fan-outs one sub-job per
    # element of the arrayed inputs. The pack author is contractually
    # promising that shards do NOT share state or exchange data. See
    # ``docs/writing-a-pack.md#writing-an-arrayable-pack``.
    arrayable: bool = False

    inputs: dict[str, InputSpec] = Field(default_factory=dict)
    outputs: dict[str, OutputSpec]
    params: dict[str, ParamSpec] = Field(default_factory=dict)
    runtime: RuntimeSpec
    exec: ExecSpec
    idempotency: IdempotencySpec | None = None
    previews: list[PreviewSpec] = Field(default_factory=list)
    progress: ProgressSpec | None = None

    @model_validator(mode="after")
    def _check_tags_from_refs(self) -> Manifest:
        """Every ``tags_from`` on an output must reference a real input port."""
        input_names = set(self.inputs)
        for out_name, spec in self.outputs.items():
            if spec.tags_from is not None and spec.tags_from not in input_names:
                raise ValueError(
                    f"output port {out_name!r} has tags_from={spec.tags_from!r} "
                    f"which is not a declared input port on this pack"
                )
        return self

    @field_validator("apiVersion")
    @classmethod
    def _check_api_version(cls, v: str) -> str:
        if v != API_VERSION:
            raise ValueError(
                f"unsupported apiVersion {v!r} (this HoloLab supports {API_VERSION!r})"
            )
        return v

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        allowed = set("abcdefghijklmnopqrstuvwxyz0123456789_-")
        if not v or any(c not in allowed for c in v):
            raise ValueError(f"pack name {v!r} must be lowercase [a-z0-9_-]+")
        return v

    @field_validator("category", mode="before")
    @classmethod
    def _normalize_category(cls, v: Any) -> list[str]:
        # Accept either a slash-hierarchy string ("reconstruction/sharp-4dgs")
        # or a pre-split list of segments (["reconstruction", "sharp-4dgs"]).
        # Canonical wire form is always a list of segments so the frontend
        # tree can walk it without re-parsing.
        if v is None:
            return []
        if isinstance(v, str):
            if not v.strip():
                return []
            segments = v.split("/")
        elif isinstance(v, list):
            segments = [str(s).strip() for s in v]
        else:
            raise ValueError(
                f"category must be a string ('a/b/c') or a list of segments, got {type(v).__name__}"
            )
        allowed = set("abcdefghijklmnopqrstuvwxyz0123456789_-")
        for seg in segments:
            if not seg:
                raise ValueError("category segments must be non-empty")
            if any(c not in allowed for c in seg):
                raise ValueError(
                    f"category segment {seg!r} must be lowercase [a-z0-9_-]+"
                )
        return segments

    @field_validator("version")
    @classmethod
    def _check_semver(cls, v: str) -> str:
        parts = v.split(".")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            raise ValueError(f"version {v!r} must be semver MAJOR.MINOR.PATCH")
        return v

    @model_validator(mode="after")
    def _check_outputs(self) -> Manifest:
        if not self.outputs:
            raise ValueError("manifest must declare at least one output")
        return self


def load_manifest(path: Path) -> tuple[Manifest, str]:
    """Load and validate a ``manifest.yaml``.

    Args:
        path: Path to a ``manifest.yaml`` file.

    Returns:
        A tuple of ``(manifest, sha256_hex)`` where the hash covers the raw
        file bytes and is used as ``manifest_hash`` in the pack inventory.

    Raises:
        FileNotFoundError: if ``path`` does not exist.
        ValueError: on YAML parse errors or schema validation failure.

    Unknown top-level manifest fields are dropped (see the ``extra=``
    policy on :class:`Manifest`) and surfaced via ``logging.warning`` so
    typos or forward-schema-incompat pack authors notice while running
    an older node.
    """

    if not path.is_file():
        raise FileNotFoundError(f"manifest not found: {path}")

    raw = path.read_bytes()
    manifest_hash = hashlib.sha256(raw).hexdigest()

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ValueError(f"manifest YAML parse error in {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"manifest {path} must be a YAML mapping, got {type(data).__name__}")

    # Top-level unknown-key warning — captures the common "new pack
    # authored against a schema version this node doesn't know" case.
    # Nested unknowns (e.g. ``runtime.something-new``) are dropped
    # silently to keep the walk cheap; deep-diffing every nested model
    # against the raw dict adds complexity for a case pack authors
    # rarely hit.
    known_top = set(Manifest.model_fields.keys())
    unknown = [k for k in data if k not in known_top]
    if unknown:
        import logging

        logging.getLogger("hololab.manifest").warning(
            "manifest %s: dropping unknown top-level field(s) %s "
            "(this node's schema knows: %s). Update HoloLab to pick these up.",
            path,
            sorted(unknown),
            sorted(known_top),
        )

    try:
        manifest = Manifest.model_validate(data)
    except Exception as exc:
        raise ValueError(f"manifest {path} validation failed: {exc}") from exc

    return manifest, manifest_hash
