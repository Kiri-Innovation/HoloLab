"""Pack manifest schema — the source of truth for pack validation.

The models here mirror ``docs/pack-spec.md``. Changes to either must land
together.
"""

from __future__ import annotations

import hashlib
import math
from enum import Enum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hololab.manifest.requirements import Requirements
from hololab.package_identity import validate_name, validate_version

API_VERSION = "hololab.dev/v1"


class ParamType(str, Enum):
    """Closed set of parameter types.

    Scalars, native arrays, plus a small list wrapper: ``list[str]`` for structural knobs
    like ``regroup.input_dims`` where the value is a small ordered list
    of dim labels. Ports are still typed by tag; params never carry tags.
    """

    ARRAY = "array"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    STRING = "string"
    PATH = "path"
    ENUM = "enum"
    # Small ordered list of strings — used by structural knobs like
    # ``regroup.input_dims`` / ``regroup.output_dims`` that name arrayed
    # layers. Not a general-purpose "any-typed list" — keep to str for
    # now so the NodeInspector's ``DimListEditor`` widget stays simple.
    LIST_STR = "list[str]"


class StorageForm(str, Enum):
    """How an output/input is physically stored on disk.

    This is an internal transport hint — it drives whether cross-node
    materialization pulls a single file or a tarball of a directory tree.
    Users authoring a graph never see it; it does NOT participate in edge
    compatibility (tags and collection axes do). The image contract additionally
    requires file storage for a single image and dir storage for an array.
    """

    DIR = "dir"
    FILE = "file"


# ---------------------------------------------------------------------------
# Ports and params
# ---------------------------------------------------------------------------


class InputSpec(BaseModel):
    """Declared input port on an algorithm node.

    ``tags`` and ``arrayed``/``dim_labels`` describe ONE invocation. Each
    collection axis is a directory-container layer; leaves can be files (image)
    or opaque bundles. ``image`` means one fully decodable image file.

    Node-level fan-out adds one outer collection. ``scalar`` on an input means
    broadcast this complete declared value to each invocation, without removing
    its own collection layers. ``any`` is an explicitly unconstrained leaf.
    Existing ``type`` / ``optional`` legacy fields remain ignored.
    """

    model_config = ConfigDict(extra="ignore")

    tags: list[str] = Field(default_factory=list)
    required: bool = True
    inline: bool | None = None
    inline_default: Any = None
    # Named collection axes must agree unless this input explicitly opts out.
    require_axis_label_match: bool = Field(default=True, strict=True)
    storage: StorageForm = StorageForm.DIR
    description: str | None = None
    arrayed: bool = False
    scalar: bool = False
    # Single-invocation collection labels, outer first. Node fan-out is separate.
    dim_labels: list[str] = Field(default_factory=list)
    # Legacy generic layout hint. It never changes fan-out enumeration depth;
    # image declarations reject it (images have no intrinsic collection axes).
    content_dims_from: str | None = None

    @model_validator(mode="after")
    def _inline_declaration(self) -> InputSpec:
        from types import SimpleNamespace

        from hololab.inline_values import input_literal, supports_inline

        if self.inline is True and not supports_inline(self):
            raise ValueError("inline supports only float/vec3 value ports")
        if self.inline_default is not None:
            input_literal(SimpleNamespace(port_values={}), "default", self)
        return self

    @model_validator(mode="after")
    def _tags_non_empty(self) -> InputSpec:
        if not self.tags:
            raise ValueError("input port must declare at least one tag (object type)")
        from hololab.manifest.data_contract import check_declaration

        check_declaration(self)
        return self

    @model_validator(mode="after")
    def _dim_labels_consistent(self) -> InputSpec:
        if len(self.dim_labels) > 2:
            raise ValueError(
                "dim_labels currently supports at most 2 layers "
                f"(got {len(self.dim_labels)} — {self.dim_labels!r})"
            )
        return self


class OutputPreview(BaseModel):
    """How the frontend should render this output when the node is expanded.

    Optional per-output declaration. Absence = the canvas node has no
    expand affordance (the "not all nodes support preview" contract from
    the design). Presence = the node grows a small caret; expanding
    reveals the named viewer with the output handle streamed via the
    gateway's ``/proxy/{node}/...`` route.

    Fields:
        viewer  — one of ``splatv | lagsplat | video | image | text | video-grid``.
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

    viewer: Literal["splatv", "ply-3dgs", "lagsplat", "video", "image", "text", "video-grid"]
    member: str | None = None


class OutputSpec(BaseModel):
    """Declared output port on an algorithm node.

    Same semantics as :class:`InputSpec`: ``tags`` names the object type
    this port produces, ``storage`` is the internal transport hint.
    ``preview`` (optional) opts the port into the in-canvas viewer.

    ``arrayed`` marks the port as producing an ``arrayed<T>`` value; same
    per-node override rule as inputs (see :class:`InputSpec` docstring).

    Every output describes one invocation. Fan-out aggregates one value per
    shard in an additional outer directory. The legacy output ``scalar`` flag
    never suppresses this aggregation or erases declared array dimensions.

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
    scalar: bool = False
    tags_from: str | None = None
    # Single-invocation collection axes; fan-out adds its own outer layer.
    dim_labels: list[str] = Field(default_factory=list)
    # Names a params key whose runtime value (a list[str]) overrides
    # dim_labels at display time.  Used by reshape packs (e.g. regroup)
    # whose output axis labels are caller-supplied via params rather than
    # fixed at pack-authoring time.  Gateway resolves this from the
    # producing job's params dict when building handle chip metadata.
    dim_labels_from: str | None = None
    # Names an INPUT port on the same pack whose effective dim_labels
    # this output inherits (minus ``dim_labels_drop_outer`` outer layers).
    # Used by element-access packs (``get-index``) whose output dim
    # structure mirrors the input tree with the outermost layer collapsed
    # — the framework resolves the upstream producer's effective labels
    # by walking the wire, so downstream consumers see the reduced
    # ``arrayed<T>`` structure without the pack author re-declaring it.
    # Mutually exclusive with ``dim_labels_from``.
    dim_labels_from_input: str | None = None
    # Number of OUTER dim layers to drop when deriving from
    # ``dim_labels_from_input``. Zero-index-aware: 0 means "same shape as
    # input" (a passthrough / slice-in-place). ``get-index`` sets this to
    # 1 (collapses the outermost dim by picking one element). Ignored
    # when ``dim_labels_from_input`` is unset.
    dim_labels_drop_outer: int = 0

    @model_validator(mode="after")
    def _tags_non_empty(self) -> OutputSpec:
        if not self.tags:
            raise ValueError("output port must declare at least one tag (object type)")
        from hololab.manifest.data_contract import check_declaration

        check_declaration(self)
        return self

    @model_validator(mode="after")
    def _dim_labels_consistent(self) -> OutputSpec:
        if len(self.dim_labels) > 2:
            raise ValueError(
                "dim_labels currently supports at most 2 layers "
                f"(got {len(self.dim_labels)} — {self.dim_labels!r})"
            )
        return self

    @model_validator(mode="after")
    def _derived_dims_consistent(self) -> OutputSpec:
        if self.dim_labels_from_input is not None and self.dim_labels_from is not None:
            raise ValueError(
                "dim_labels_from_input and dim_labels_from are mutually exclusive "
                "(pick one source: input-port derivation vs. params key)"
            )
        if self.dim_labels_drop_outer < 0:
            raise ValueError(
                f"dim_labels_drop_outer must be >= 0 (got {self.dim_labels_drop_outer})"
            )
        if self.dim_labels_from_input is None and self.dim_labels_drop_outer != 0:
            raise ValueError(
                "dim_labels_drop_outer is only meaningful with dim_labels_from_input; "
                "leave it at 0 or set the input source"
            )
        return self


class ParamSpec(BaseModel):
    """Declared user-facing parameter for an algorithm node.

    Params are scalar knobs or homogeneous scalar arrays, completely distinct
    from ports. They never carry tags.
    """

    model_config = ConfigDict(extra="ignore")

    type: ParamType
    items: ParamType | None = None
    default: Any = None
    description: str | None = None
    min: float | int | None = None
    max: float | int | None = None
    values: list[Any] | None = None  # required for enum
    optional: bool = False
    pinned: bool = Field(default=False, strict=True)

    @model_validator(mode="before")
    @classmethod
    def reject_pin_aliases(cls, value: Any) -> Any:
        if isinstance(value, dict):
            for key in value:
                if key != "pinned" and (key.startswith("pin") or "pinned" in key):
                    raise ValueError(f"Unknown pin field {key!r}; use pinned: true/false")
        return value

    must_exist: bool | None = Field(default=None, strict=True)
    path_kind: Literal["any", "file", "directory"] | None = None

    def validate_array_value(self, name: str, value: Any) -> None:
        """Strict native arrays only; existing scalar validation is unchanged."""
        if self.type is not ParamType.ARRAY:
            return
        if value is None and self.optional:
            return
        if not isinstance(value, list):
            raise ValueError(f"parameter {name!r}: expected an array")
        for index, item in enumerate(value):
            label = f"{name}[{index}]"
            valid = {
                ParamType.STRING: isinstance(item, str),
                ParamType.PATH: isinstance(item, str),
                ParamType.INT: type(item) is int,
                ParamType.FLOAT: type(item) in (int, float),
                ParamType.BOOL: type(item) is bool,
                ParamType.ENUM: True,
            }.get(self.items, False)
            if not valid:
                raise ValueError(f"parameter {label!r}: expected {self.items.value}")
            if self.items in (ParamType.INT, ParamType.FLOAT):
                try:
                    finite = math.isfinite(item)
                except OverflowError:
                    finite = False
                if not finite:
                    raise ValueError(f"parameter {label!r}: expected a finite number")
                if self.min is not None and item < self.min:
                    raise ValueError(f"parameter {label!r}: must be >= {self.min}")
                if self.max is not None and item > self.max:
                    raise ValueError(f"parameter {label!r}: must be <= {self.max}")
            if self.items is ParamType.ENUM:
                self.model_copy(update={"type": ParamType.ENUM}).validate_enum_value(label, item)

    @model_validator(mode="after")
    def _check_array(self) -> ParamSpec:
        if self.type is not ParamType.ARRAY:
            if self.items is not None:
                raise ValueError("items applies only to array parameters")
            return self
        if self.items is None or self.items in (ParamType.ARRAY, ParamType.LIST_STR):
            raise ValueError("array items must declare a scalar parameter type")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("array element min must be <= max")
        if self.items is ParamType.ENUM:
            if not self.values:
                raise ValueError("array of enum must declare values")
            if any(type(v) not in (str, int, float, bool) for v in self.values):
                raise ValueError("array enum values must be scalars")
        if self.default is not None:
            self.validate_array_value("default", self.default)
        return self

    def validate_enum_value(self, name: str, value: Any) -> None:
        """Validate a new dispatch, without changing persisted workflow values."""
        if self.type is not ParamType.ENUM:
            return
        # bool is an int in Python, but not a numeric enum value on the wire.
        if not any(
            value == option
            and (
                type(value) is type(option)
                or (type(value) in (int, float) and type(option) in (int, float))
            )
            for option in (self.values or [])
        ):
            raise ValueError(
                f"parameter {name!r}: value {value!r} is not allowed; allowed values: {self.values!r}"
            )

    @model_validator(mode="after")
    def _check_path(self) -> ParamSpec:
        if self.type is ParamType.PATH:
            if self.default is not None and not isinstance(self.default, str):
                raise ValueError("path default must be a string")
        elif (self.must_exist is not None or self.path_kind is not None) and not (
            self.type is ParamType.ARRAY and self.items is ParamType.PATH
        ):
            raise ValueError("must_exist/path_kind apply only to path parameters")
        return self

    @model_validator(mode="after")
    def _check_enum(self) -> ParamSpec:
        if self.type is ParamType.ENUM and not self.values:
            raise ValueError("param of type 'enum' must declare 'values'")
        return self

    @model_validator(mode="after")
    def _check_list_str_default(self) -> ParamSpec:
        if (
            self.type is ParamType.LIST_STR
            and self.default is not None
            and (
                not isinstance(self.default, list)
                or not all(isinstance(v, str) for v in self.default)
            )
        ):
            raise ValueError(
                "param of type 'list[str]' must have a list-of-strings default "
                f"(got {type(self.default).__name__}: {self.default!r})"
            )
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

    mode: Literal["conda", "host"] = "conda"
    env: str | None = None  # logical conda environment; absent for bundled host jobs

    @model_validator(mode="after")
    def _check_mode(self):
        if self.mode == "conda" and not self.env:
            raise ValueError("conda runtime requires env")
        if self.mode == "host" and self.env is not None:
            raise ValueError("host runtime cannot declare a conda env")
        return self

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

    shell: str = ""
    python: str | None = None  # package-relative entrypoint, never an interpreter path
    args: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_command(self):
        if bool(self.shell) == bool(self.python):
            raise ValueError("exec requires exactly one of shell or python")
        if self.args and not self.python:
            raise ValueError("exec.args requires python")
        return self

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


class NodeViewerSpec(BaseModel):
    """A pack-owned viewer mounted below an algorithm node.

    Unlike :class:`OutputPreview`, a node viewer is not an output port and
    therefore has no graph/wiring semantics.  ``entry`` is a file relative to
    the pack directory; the gateway proxies it from the node file server.
    ``live_globs`` are optional templated paths watched by the node runtime
    while a job is running.  Every stable matching file is announced to the
    iframe as an image update.
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    title: str | None = None
    default: bool = False
    renderer: Literal["iframe"]
    entry: str
    # Optional durable output used to rehydrate the viewer after a browser
    # refresh; it remains an ordinary output, not a viewer-only port.
    history_output: str | None = None
    # Optional JSON index relative to ``history_output``.  This permits a
    # viewer to retain private files inside a semantic output directory
    # without promoting the files themselves to a graph output port.
    history_index: str | None = None
    live_globs: list[str] = Field(default_factory=list)

    @field_validator("entry")
    @classmethod
    def _relative_entry(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("node_viewers.entry must be a relative path inside the pack")
        return value

    @field_validator("history_index")
    @classmethod
    def _relative_history_index(cls, value: str | None) -> str | None:
        if value is None:
            return value
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("node_viewers.history_index must be relative to history_output")
        return value


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
    root: str = "."
    cold: list[str] = Field(default_factory=list)
    external_code: list[dict[str, Any]] = Field(default_factory=list)
    requires: Requirements | None = None

    @field_validator("cold")
    @classmethod
    def _cold_paths(cls, values: list[str]) -> list[str]:
        for value in values:
            if not value or Path(value).is_absolute() or ".." in Path(value).parts:
                raise ValueError("cold entries must be relative paths inside root")
        return values

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
    node_viewers: list[NodeViewerSpec] = Field(default_factory=list)
    # A pack-owned node viewer can be the complete card-bottom experience.
    # Keep ordinary output inspection visible by default; packs must opt in
    # explicitly when their diagnostic viewer intentionally replaces it.
    hide_default_output_previews: bool = False
    previews: list[PreviewSpec] = Field(default_factory=list)
    progress: ProgressSpec | None = None

    @model_validator(mode="after")
    def _check_host_exec(self):
        if self.runtime.mode == "host":
            from hololab.bundled import is_builtin

            if not is_builtin(self.name) or not self.exec.python:
                raise ValueError("host runtime is reserved for bundled Python packages")
            if self.arrayable:
                raise ValueError("host packages do not support batched arrayable execution")
        elif self.exec.python:
            raise ValueError("exec.python requires host runtime")
        return self

    @model_validator(mode="after")
    def _check_tags_from_refs(self) -> Manifest:
        """Every ``tags_from`` on an output must reference a real input port."""
        input_names = set(self.inputs)
        output_names = set(self.outputs)
        for viewer in self.node_viewers:
            if viewer.history_output is not None and viewer.history_output not in output_names:
                raise ValueError(
                    f"node viewer {viewer.id!r} references unknown history_output="
                    f"{viewer.history_output!r}"
                )
        for out_name, spec in self.outputs.items():
            if spec.tags_from is not None and spec.tags_from not in input_names:
                raise ValueError(
                    f"output port {out_name!r} has tags_from={spec.tags_from!r} "
                    f"which is not a declared input port on this pack"
                )
            if (
                spec.dim_labels_from_input is not None
                and spec.dim_labels_from_input not in input_names
            ):
                raise ValueError(
                    f"output port {out_name!r} has dim_labels_from_input="
                    f"{spec.dim_labels_from_input!r} which is not a declared "
                    f"input port on this pack"
                )
        for in_name, spec in self.inputs.items():
            if spec.content_dims_from is None:
                continue
            param = self.params.get(spec.content_dims_from)
            if param is None:
                raise ValueError(
                    f"input port {in_name!r} has content_dims_from="
                    f"{spec.content_dims_from!r} which is not a declared param"
                )
            if param.type is not ParamType.INT:
                raise ValueError(
                    f"input port {in_name!r} content_dims_from must name an int param "
                    f"(got {param.type.value!r})"
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
        return validate_name(v)

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
                raise ValueError(f"category segment {seg!r} must be lowercase [a-z0-9_-]+")
        return segments

    @field_validator("version")
    @classmethod
    def _check_semver(cls, v: str) -> str:
        return validate_version(v)

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
