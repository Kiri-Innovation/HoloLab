---
title: Pack 规范
group: 接口与格式
order: 10
---
# Pack Specification (`hololab.dev/v1`)

**New to HoloLab pack authoring?** Start with the friendlier walkthrough
in [`writing-a-pack.md`](writing-a-pack.md) — it covers the 30-second
"put a directory somewhere and register the path" flow, common pitfalls,
and how the multi-source scanner works. This document is the field
reference the frontend + node both validate against.

An algorithm **pack** is a self-contained, machine-independent description of one algorithm. A pack is a directory containing at minimum:

```
my-algo@0.1.0/
├── manifest.yaml   # the pack contract (this document defines its schema)
└── ...             # any supporting files your command needs
```

Pack identity comes from `name` and `version` in the manifest. `NAME@VERSION` is a directory naming convention, not a scanner requirement. A source may be a manifest file, a directory containing `manifest.yaml`, or a directory of Pack subdirectories. Multiple versions can coexist; duplicate identities across sources resolve in configured source order (first wins).

## Design principles

1. **Machine-independent.** No absolute paths, no assumptions about where conda lives. Machine-specific mappings live in the node config, never here.
2. **Declarative, not code.** The manifest describes *what* — the node runs *how*. HoloLab never imports pack code.
3. **Object-in, object-out.** Ports carry **objects** identified by their **tags**. Whether an object lives as a file or a directory (or, later, in an object store) is a system implementation detail — pack authors never write logic that cares. Users likewise never care where the bytes physically live.
4. **Idempotent by convention.** If your command wrote a `.done` marker, we may skip it next time. See [Idempotency](#idempotency).

## Minimum example

```yaml
apiVersion: hololab.dev/v1
kind: Algorithm
name: my-algo
version: 0.1.0
description: "One-line summary shown in the palette."

inputs:
  input_dir:
    tags: [colmap]

params:
  iterations:
    type: int
    default: 1000

outputs:
  result_dir:
    tags: [my_output]

runtime:
  env: kiri
  gpu: { required: true, vram_gb_min: 6 }

exec:
  shell: |
    python my_script.py \
      --input {{ inputs.input_dir }} \
      --output {{ outputs.result_dir }} \
      --iterations {{ params.iterations }}

idempotency:
  marker: "{{ outputs.result_dir }}/.done"

previews:
  - id: thumb
    type: image
    path: "{{ outputs.result_dir }}/thumb.png"
```

## Fields

### Top-level

| Field         | Required | Description                                                            |
| ------------- | -------- | ---------------------------------------------------------------------- |
| `apiVersion`  | Yes      | Must be `hololab.dev/v1`.                                              |
| `kind`        | Yes      | Must be `Algorithm`.                                                   |
| `name`        | Yes      | Package name, lowercase, `[a-z0-9_-]+`.                                |
| `version`     | Yes      | Semver `MAJOR.MINOR.PATCH`.                                            |
| `description` | No       | One-line summary shown in UI palette.                                  |
| `category`    | No       | Slash-hierarchy path for the palette tree (e.g. `reconstruction/sharp-4dgs`). See [Category](#category). |
| `docs`        | No       | Short Markdown blurb shown in the Inspector's About block. See [Docs](#docs). |
| `source_entry`| No       | ⌘/Ctrl+click "Jump to source" target (the pack's core implementation script). Relative paths resolve against **the manifest.yaml's own directory**; absent → modifier+click opens the manifest's directory. |
| `inputs`      | No       | Map of input name → input spec.                                        |
| `outputs`     | Yes      | Map of output name → output spec. Must have ≥ 1 output.                |
| `params`      | No       | Map of param name → param spec.                                        |
| `runtime`     | Yes      | Runtime requirements. See [Runtime](#runtime).                         |
| `exec`        | Yes      | Command template. See [Exec](#exec).                                   |
| `idempotency` | No       | Marker convention for skipping re-runs. See [Idempotency](#idempotency). |
| `previews`    | No       | List of preview declarations. See [Previews](#previews).               |
| `progress`    | No       | How the node parses progress from your output. See [Progress](#progress). |

### Category

`category` is an optional slash-separated path that places the pack inside the
palette's tree view. It's presentation-only — the runtime never reads it.

```yaml
category: reconstruction/sharp-4dgs   # preferred: string with '/' separators
category: [reconstruction, sharp-4dgs] # also accepted (canonical wire form)
```

Rules:

- Each segment must match `[a-z0-9_-]+` (same charset as `name`). No spaces, no
  slashes inside a segment, no uppercase.
- Empty segments are rejected (`a//b`, leading/trailing `/`).
- Omit the field or use `uncategorized` for packs without a natural home; the
  palette groups those under a `misc` fallback.

Existing convention (see `packs/`):

| Prefix           | Meaning                                                     |
| ---------------- | ----------------------------------------------------------- |
| `source/`        | Data-source nodes (video, dataset pointer, …).              |
| `tracking/`      | Camera-pose / SfM stages.                                   |
| `reconstruction/`| Producing gaussians / colmap / mesh outputs.                |
| `training/`      | Training a 3D/4D representation.                            |
| `export/`        | Converting a trained model to a viewer-ready format.        |
| `demo/`          | Smoke-test packs (kept together, not shipped as pipeline).  |

### Docs

`docs` is an optional Markdown string rendered in the node Inspector's
**About** block (below the compute-node picker) whenever the pack is
selected on the canvas. Keep it short — a few lines is the sweet spot:
one sentence on what the algorithm does, what it reads / writes, key
knobs, and any gotchas an operator should know before dispatching a job.

```yaml
docs: |
  Trains a SpacetimeGaussians 4D model on a COLMAP-shaped dataset.

  - **Input:** container dir with `colmap_<start_time>` subdirs.
  - **Output:** `point_cloud/iteration_N/point_cloud.ply` under `model_dir`.
  - Set `iterations` low (~5k) for smoke tests; production runs use 25k+.
  - Requires a CUDA GPU; expect one hour per 25k iterations on an A100.
```

The renderer supports headings (`#`, `##`), paragraphs, unordered/ordered
lists, links, `inline code`, `**bold**`, and fenced code blocks. No HTML,
no images — intentionally minimal so pack authors don't have to think
about what will and won't survive the round-trip. The runtime never
reads `docs`; it's presentation-only.

### Ports — tags are the object type

Every port (input or output) is typed by its **tags** — a list of strings that
name the object type the port carries. Compatibility checks effective tags,
array cardinality, and collection dimension depth/labels (see [Dimensions](#dimensions-dim_labels)).
Tags match by non-empty intersection or the `any` wildcard.
`image_sequence` and `frame_sequence` are removed type tags and are rejected,
even alongside `any`. Port variable names such as `frame_sequence` remain valid. A tag match alone does
not make an array/scalar mismatch valid; a named-axis mismatch requires the
consumer's explicit `require_axis_label_match: false` opt-out.

A port can carry multiple tags at once. Semantically this reads as "this
thing is BOTH an X AND a Y". A specialized producer might declare
`tags: [colmap, sharp4dgs_export]` — meaning it produces a colmap directory
that is specifically a sharp4dgs export. Any consumer that wants `[colmap]`
or `[sharp4dgs_export]` (or both) accepts it.

`tags`, `arrayed`, `scalar`, and dimension/derivation fields together form the
machine-readable port contract. There is no `type: "arrayed<...>"` string grammar.
`PathDir`/`PathFile`/etc. are not object tags — how the object is stored on disk
is a separate system detail.
For CV pipeline evolution: pack authors should think in terms of the object
they're moving (colmap, gaussians, mask sequence, splatv) rather than files
vs directories.

```yaml
inputs:
  colmap:              # port variable name (used in shell template)
    tags: [colmap]     # object type
    required: true     # default; write `required: false` for optional ports

  optional_flow:
    tags: [t4w_flow]
    required: false
    description: "Track4World flow — if provided, initializes better."

outputs:
  scene:
    tags: [colmap, sharp4dgs_export]
    description: "A colmap dataset, specifically the sharp4dgs export flavour."
```

#### The internal `storage` hint (not user-facing)

Objects have a physical shape on disk — usually a directory tree (colmap,
gaussians), occasionally a single file (splatv, video). This is HoloLab's
concern, not yours: when a downstream node on a different machine needs the
object, HoloLab moves it correctly (tarball for a directory, single fetch
for a file). You should not need to look at it in most cases.

If your algorithm writes a single file rather than a directory tree, declare
`storage: file` on the output so the transport layer knows to fetch it as a
single file rather than a tarball:

```yaml
outputs:
  splatv:
    tags: [splatv]
    storage: file      # optional; defaults to `dir`
```

`storage` is **not** part of edge compatibility. It does not override tag,
cardinality or dimension checks.

### `arrayed` and `arrayable`

Every port declaration describes **one algorithm invocation**. `image` strictly
means one fully decodable, single-frame image **file**, with `storage: file`.
It never means a directory of images and has no intrinsic image-count axis.
A collection must declare its directory and axes explicitly:

```yaml
inputs:
  frames:
    tags: [image]
    arrayed: true
    storage: dir
    dim_labels: [cam]
```

An `arrayed<T>` is a directory of non-dot entries of type T. Entries can be
files (image leaves), directories (bundles or nested collections), or symlinks
to valid entries. Element IDs are sorted entry names, including file extensions.

- Per-port `arrayed: true` declares a collection consumed/produced in one call.
- Per-pack `arrayable: true` enables node `arrayed_toggle`. Turning it on adds
  **one outer collection layer**, even if the invocation already consumes an
  array. A `[cam]` image invocation then consumes one element of `[frame,cam]`.
- An input with `scalar: true` broadcasts its entire declared value to every
  shard. Its declared collection layers remain intact; broadcast does not allow
  an array to satisfy a single-image contract. Use `arrayfy` for explicit wrapping.

The system guarantees actual delivered data conforms to the declaration. The
algorithm developer owns the declaration's agreement with algorithm code; the
system does not infer intent or statically analyze that code. Checks run when
loading declarations, validating edges, before execution and before publishing
any output (including cached output). Image arrays are validated **at every
leaf**, including full decode, not by sampling handle summaries.

Shard mechanics:

- Framework mints one parent job (coordinator, no compute-node dispatch)
  plus N shard jobs, one per element of the arrayed inputs.
- Each shard writes its outputs to `{parent_workspace}/{port}/{element_id}/`
  instead of its own workspace (via a `shard_output_prefix` on `JobAssign`).
  Aggregation is automatic: after all shards finish, the framework registers
  one parent output handle per port pointing at `{parent_workspace}/{port}/`.
- `parallelism` bounds concurrent shard dispatch. Shard jobs are created up
  front; one failure does not short-circuit the pool. The parent is marked
  failed after all shards reach terminal states; downstream nodes are not
  started from a partially successful parent.

The pack shell may reference `{{ shard.element_id }}` and `{{ shard.index }}`
when executing as a shard. The `shard` binding is absent for non-shard runs;
Jinja2 StrictUndefined raises an explicit template error if those runs reference
`shard.*`, rather than substituting an empty string.

The `arrayable: true` contract obligates the pack author to promise **no
cross-shard state or data exchange** — the framework has no way to enforce
this (shell can do anything), but violating it produces subtle bugs.

**Per-port `scalar: true`** on an input selects broadcast, preserving the
single-invocation type. The legacy output flag does not suppress aggregation:
every fan-out output gains one outer collection layer. Declare only the
invocation dimensions on outputs; do not predeclare the framework's outer axis.

### Dimensions (`dim_labels`)

<a id="dim-labels"></a>

The YAML contract remains structured fields, parsed by `InputSpec`/`OutputSpec`
in `hololab/manifest/schema.py`. Type labels such as `arrayed[frame]<image>` are
terminal renderings of that contract, not accepted YAML syntax and never parsed
back to determine execution or compatibility.

| Field | Meaning |
|---|---|
| `dim_labels: [frame, cam]` | Semantic axes, **outer first**. An empty list on an effective array retains one unnamed legacy layer. Empty label strings are wildcard names in compatibility checks. |
| `require_axis_label_match: true` | Input-only boolean, default `true`. Compare nonempty axis names at every collection layer; `false` skips names only, never depth, array/scalar shape or tags. Unnamed axes remain wildcard names in either mode. |
| `dim_labels_from: output_dims` | Output labels derive from this node's parameter list. Invalid/missing values fall back to declared labels, as before. |
| `dim_labels_from_input: frames` | Output labels derive from the connected producer's resolved output. Missing connections/cycles give unresolved labels. |
| `dim_labels_drop_outer: 1` | Remove this many outer labels after input derivation; zero preserves them, dropping all yields a scalar. Nonnegative; requires `dim_labels_from_input`. |
| `content_dims_from: content_dims` | Legacy field retained for non-image declarations; it does not change fan-out depth. Rejected for image ports. |

`dim_labels_from` and `dim_labels_from_input` are mutually exclusive. The current
manifest schema accepts at most **two static labels**. This is a YAML validation
limit, not a license to truncate runtime `dim_sizes`: runtime observations and
the recursive IR accept variable-length axes. Dynamic parameter-derived labels
retain the existing resolver behavior; this update does not add a new pack limit.

Labels describe the **single-invocation** collection axes, outer first.
A node fan-out adds one outer axis, using the driving producer's outer label
when known (otherwise an unnamed axis). The boolean `arrayed` projection alone
cannot encode depth: use the recursive `port_types` IR and its invocation and
aggregate views. Never wrap an already-resolved aggregate again.

Compatibility uses the same normalized IR as the graph API: resolve generic
tags and dimensions, require equal collection depth and matching nonempty axis
labels (empty names are wildcards), then require leaf tag overlap or `any`.
An input may explicitly set `require_axis_label_match: false` when its algorithm does
not depend on axis names. This does not relabel upstream data or reorder files;
the pack developer remains responsible for input ordering and algorithm semantics.
Bare `any` is a structural generic value; removed tags remain invalid even with
`any`. Broadcast preserves declared shape. Counts measured at runtime do not
participate in declaration compatibility.

Fan-out enumerates **exactly one outer layer**. A shard receives the declared
invocation value, which may itself be a collection; it never flattens all axes.
`dim_sizes` observations retain all measured explicit collection layers.
Unknown/sampled/complete evidence describes measurement coverage, not a type
guarantee; contract validation is separate and exhaustive.

### Declaration, effective type, and runtime observation

- **Declaration**: YAML contract and derivation directives; preserved even when
  a generic output resolves to another tag or a node is arrayed.
- **Effective type**: graph-context normalization, including arrayed participation
  and derived tags/labels. Graph `port_types` and validation share this result;
  `resolved_outputs` remains the compatible legacy projection.
- **Observation**: values read from a particular handle. Summary and inline latest
  output handles add `runtime_shape.schemaVersion = 1` with separate evidence for
  tags, axes and internal counts. This does not alter the declaration or validate
  a current draft against an old result.

For example, `dim_sizes: [100, 21]` and `dim_labels: [frame, cam]` describe
outer-first measured axes. The compact display may print inner first as
`image[cam:21][frame:100]`; this display order does not change the data order.
`pose:21` / `intr:1` are internal probe metrics, not declaration dimensions.

Evidence has three states:

| Evidence | Meaning |
|---|---|
| `unknown` | Not measured, unavailable, or legacy coverage unrecorded. Optional wire `legacyValue` is retained for compatibility; the canvas ignores it and treats unknown evidence as unmeasured. |
| `sampled` | Measured at a named relative sample, such as the first sorted leaf. Does not assert the same count for all elements. |
| `complete` | Covered the referenced handle/field scope; not a guarantee about future runs or all upstream artifacts. `source` identifies the scan, probe or metadata origin. |

Directory branching factors are checked across the directory tree. Intrinsic
content dimensions may be probed only at the first leaf: an outer size can be
complete while its inner size is sampled. Internal counts at that leaf are also
sampled. Ragged directory trees or inaccessible data do not publish a complete
shape. Zero is a measured value, not an unknown. An absent `dim_sizes` does not
prove a scalar.

Legacy `tags`, `dim_labels`, `dim_sizes`, `element_count`, `internal_count`, and
`internal_count_items` keep their existing meanings. In particular, old
`dim_sizes` can contain a first-leaf content measurement; consumers requiring
coverage must read `runtime_shape`, not assume all legacy numbers are exhaustive.
See [type IR](type-ir.md) for wire fields, renderers and compatibility boundaries.

### The `int` scalar handle type

An `int` handle is `storage: file` + `tags: [int]` + a plain-text file
whose sole content is one base-10 integer (trailing newline is fine).
Producers use `echo N > "{{ outputs.n }}"`, consumers `n=$(cat "{{ inputs.n }}")`.
`GET /api/handles/{id}/summary` returns `{"kind": "scalar-int", "fields":
{"value": <int>}}` so the preview drawer shows the number without a bytes
download; parse failures surface as `fields.error` + `fields.raw`.

Introduced by the arrayed<T> type system so nodes like `array-length`
(`arrayed<T> → int`) can flow counts into `arrayfy` (`T + int → arrayed<T>`).

### Generic ports via `any` + `tags_from`

Utility packs that operate over *any* element type (`arrayfy`,
`get-index`, `array-length`) declare their generic input as `tags: [any]`,
which matches any downstream/upstream port at edge time. To make the
propagated element type flow through, the pack's *output* declares
`tags_from: <input_port_name>` — at validation time the framework walks
the wire back through the referenced input to the ultimate concrete
producer and adopts its tag set as the effective output tag set. Cycles
collapse to `[any]` (safety net).

Example — `arrayfy` (`T + int → arrayed<T>`):

```yaml
inputs:
  data:
    tags: [any]
  count:
    tags: [int]
outputs:
  out:
    tags: [any]
    arrayed: true
    tags_from: data   # ← output element type = whatever's wired into `data`
```

When nothing is wired into `data` yet, `arrayfy.out` stays `[any]` so the
canvas remains permissive during graph construction; once `data` is wired,
mismatched downstream types are surfaced by snapshot validation.

### Params — scalar and array controls, distinct from ports

Params are typed scalars — `int`, `float`, `bool`, `string`, `enum`, `path`. They
never carry tags; they never appear as connectable ports. Pack authors
declare them for the user's inspector form.

```yaml
params:
  iterations:
    type: int
    default: 1000
    min: 1
  mode:
    type: enum
    values: ["fast", "quality"]
    default: "quality"
```

### Native array parameters (IR semantics)

Declare `type: array` and `items: int|float|bool|string|enum|path`. Values and
`default` are JSON arrays, not JSON strings. Nested arrays, object elements and
`list[str]` elements are rejected. The existing specialized `list[str]` parameter
and scalar behavior remain unchanged; this has no port/tag/connection semantics.

```yaml
params:
  offsets:
    type: array
    items: float
    default: [0, 0.5]
    min: 0
  paths:
    type: array
    items: path
    default: []
    path_kind: file
```

`min`/`max` constrain each numeric element (inclusive), not array length. Numeric
values must be finite; booleans and numeric strings are not numbers. `values`
applies to every enum element. `must_exist`/`path_kind` apply to every path and
are checked on the executing node. Array defaults are validated when loading a
manifest; execution validates supplied values before starting a process. Empty
arrays are legal in the IR; a pack may require nonempty inputs. `optional: true`
permits an absent/null whole array, never null elements. Omitted defaults remain
null; authors should use `default: []` for initially empty editors.

Inspector uses the same scalar controls for each row, with remove and append
buttons. Invalid stored values are preserved until explicitly replaced.

The initial three source packs retain execution compatibility with their legacy
JSON strings through a name/version-scoped adapter. `video-array-assemble` now
uses `paths: array<path>`; `float-array-source` uses `items: array<float>` and
optional `ids: array<string>`. `vec3-array-source` uses three equal-length arrays
`x`, `y`, `scale` plus optional `ids`, avoiding nested/object arrays or a new
vector scalar. Inspector presents these three arrays as one x/y/scale row per
transform and appends/removes all three components together. Empty IDs bind sorted reference videos; explicit IDs retain their
original alignment contract. Legacy values are not rewritten by workflow GET or
execution; editing them in Inspector saves native values through normal autosave.
Deploy the updated parameter schema/runtime before relying on new manifests;
an already-running old Python process cannot learn a new enum member by hot
reloading manifests alone.

### Path parameters

`path` is a parameter UI/validation hint, not a port type or Type IR tag.
Workflow JSON still stores an ordinary string; existing string values need no
migration. Paths refer to the **assigned compute node**, not the browser host.

```yaml
params:
  video_path:
    type: path
    must_exist: true
    path_kind: file
```

`must_exist` defaults to true; false permits a future output path. `path_kind`
is `any` (default), `file`, or `directory`. These fields only apply to path
parameters. Existing paths must match the kind even when `must_exist` is false.
The node accepts absolute paths and `~` home expansion, follows symlinks and
checks existence before starting the pack. Missing, empty, non-string and
wrong-kind values produce a readable job error. OS access errors are reported;
validation cannot prevent files changing before execution.

The Web control provides prefix suggestions (Tab/Enter to complete) and a `+`
browser with location, breadcrumbs and keyboard selection. It requires an
assigned online node. `GET /api/nodes/{node_id}/paths?path=...&prefix=...&offset=0&limit=100`
uses the existing registered-node WebSocket and returns metadata only: names,
kinds, sizes, modification times, symlink flags and `next_offset`.
At most 200 entries are returned and 4096 new entries examined per page.
Prefix matching is case-sensitive. Scan-order offsets are not a snapshot;
restart pagination after directory changes. Each page sorts directories first.
Inaccessible/broken entries are unavailable; symlink navigation follows targets.

The existing node-control trust boundary applies: node sessions have identity
tokens, but Gateway REST has no separate user authentication. Browsing exposes
metadata available to the node's OS account, beyond the artifact root; keep
this control plane within trusted access. No file-content, modification or
shell-execution endpoint is added. Pack commands must still serialize paths
safely: `single-video-source` uses JSON stdin to preserve shell metacharacters.

**Upgrade order:** upgrade Gateway, node and frontend before enabling a
`type: path` manifest. Old nodes reject the new enum member and their scanner
removes invalid packs from the catalog. Existing strings and the
`single-video-source@0.1.0` identity stay unchanged; a runtime upgrade is
required. A node without browsing support times out rather than browsing the
Gateway's filesystem.

### Runtime

```yaml
runtime:
  env: kiri                # LOGICAL conda env name. Node maps to real prefix.
  gpu:
    required: true         # if false, node with no GPU may take the job
    vram_gb_min: 8         # scheduler hint
  exclusive: false         # if true, only one such job runs on the node at a time
  expected_duration_min: [5, 30]   # UI hint only
```

`env` is a logical name; the node's config file maps it to a real conda
prefix. Manifests never contain absolute paths.

### Exec

```yaml
exec:
  shell: |
    python my_script.py \
      --input {{ inputs.input_dir }} \
      --output {{ outputs.result_dir }} \
      --iterations {{ params.iterations }}
  working_dir: "{{ pack_dir }}"    # optional; defaults to pack root
```

Rendered as a Jinja2 template with these bindings:

- `inputs.<name>` — absolute path to the materialized input on this node.
  Whether it originated locally or was fetched from another node is not
  visible here.
- `outputs.<name>` — absolute path to the pre-created output directory
  (or file — the node creates the parent dir; your script creates the file).
- `params.<name>` — user-supplied param value.
- `pack_dir` — absolute path to the pack directory.
- `workspace_root` — absolute path to the node's workspace root.
- `scratch_dir` — per-job intermediate output area under the workspace.
- `staging_dir` — input staging area; falls back to scratch when no separate staging root is configured.
- `job_id`, `workflow_id` — for logging purposes.

The rendered command runs through `bash -c` with a cached activation environment,
or through `conda run -p <prefix> --no-capture-output bash -lc` when the cache is
disabled or unavailable. The executor passes an explicit environment instead of
inheriting the daemon's full environment. See [Execution environment](architecture.md#execution-environment-and-cancellation).

### Idempotency

```yaml
idempotency:
  marker: "{{ outputs.result_dir }}/.done"    # optional path
```

If the marker exists after templating, the node skips the subprocess and
re-registers the existing output handles. The marker file should be written
by your command as the last step.

### Previews

> 想把已有输出固定显示在工作流画布上时，使用 `kind: "view"` 的 view
> 贴纸，而不是实现一个输入输出相同的 preview/passthrough pack。贴纸不属于
> pack 规范、没有 manifest，也不会产生 job 或产物；其完整语义见
> [workflow-schema.md](workflow-schema.md#view-stickers)。

```yaml
previews:
  - id: sample_frame
    type: image                        # image | video | text | ply | grid | log
    path: "{{ outputs.result_dir }}/sample.png"
  - id: save_cams
    type: grid
    glob: "{{ outputs.result_dir }}/save_cam/*.png"
```

Each preview declaration produces a UI card next to the node. Path/glob are
templated. Preview generation is the pack's responsibility.

#### Tag-driven preview inference (the usual case)

**Design principle: previews are chosen by the output's *type*
(tags + arrayed-ness), not by the pack.** A per-pack `preview:` block
is an escape hatch reserved for the rare case where the type's generic
viewer misses something pack-specific. If two packs emit the same type
(e.g. one produces `arrayed<arrayed<image>>` by regrouping, the other
by fan-out extraction), they get the *same* viewer with zero
per-pack effort — which is the whole point of typed ports.

Most packs should NOT declare a per-output `preview:` block. The
gateway keeps a **tag → viewer registry** (see
`hololab/gateway/tag_viewers.py`, `TAG_VIEWER_REGISTRY`); when a
manifest is silent about preview, the gateway walks the output port's
tags at catalog-build time and pulls the canonical viewer for the
first tag that has a registered entry. That way "outputs tagged
`splatv` get the splatv viewer" and "outputs tagged
`single-video-source` get the video-grid viewer" are stated ONCE
(in the registry), not per pack.

Example: the pack authors of `single-video-source` and `stg-to-splatv`
don't have to write `preview: {viewer: …}` on their outputs — the
tags do the work:

```yaml
outputs:
  videos_dir:
    tags: [video-source]      # → auto video-grid preview
    description: "…"
```

Explicit `preview:` on an output port still wins if declared; use
that only when the tag-driven default isn't right for your pack.

**Image viewers.** `image` with `storage: file` previews the file directly.
An explicit one-dimensional image array uses `FrameStripPreview`; nested image
arrays use `NestedFrameSequencePreview` with a group drill-down. Viewer selection
uses the resolved shape and storage, not removed aliases or an implicit
`frames/` directory. Frame extraction under fan-out yields `[cam,frame]` image
collections; regrouping can transpose them to `[frame,cam]`.

**Arrayed fallback — pager over the scalar viewer.** When a tag family
has a scalar viewer but *no* dedicated `arrayed<T>` viewer, the
frontend wraps the scalar in `ArrayedPaginator` (`previews.tsx`)
instead of falling back to the basic-info card: a compact
`‹ name (i/N) ›` row at the top, then the scalar viewer mounted
against the currently-selected element's subdirectory
(`<baseUrl>/<element>`). ArrowLeft/Right advance the pager while the
drawer is hovered. This is the current wiring for `colmap-points` —
`colmap-triangulate[arrayed]` fans out one `points3D.txt` per frame
and the operator flips through them via the pager while the
`ColmapPointsPreview` iframe re-injects the new file. Any tag that
declares a scalar viewer via the routing table gets this fallback for
free; opt in to a bespoke arrayed viewer (like
`NestedFrameSequencePreview`) only when the pager would drop
information the operator needs to see at a glance across elements.

Data path for `NestedFrameSequencePreview`: a single
`GET /api/handles/{id}/summary`. The server-side enrichment
(`handle_summary._list_dir_children`) attaches immediate `children`
to each directory entry so the frontend gets the per-group image list
without an extra listing round-trip. Only ~9 thumbnails (3 groups × 3
thumbs) are loaded up front; the drill-down loads at most
`STRIP_MAX_CARDS` more. No probes.

**Iframe-embedded viewers.** Some tags map to a full 3D viewer that
already exists as a standalone web app; instead of re-implementing the
renderer inside HoloLab we iframe the standalone build and inject the
handle's files via `postMessage`. Rationale: the third-party viewer
keeps evolving as its own product (own Three.js version, shaders,
worker files); vendoring only its `dist/` avoids forking hundreds of
files and hauling a large runtime submodule into HoloLab's own bundle.
`colmap-cams` is the canonical example:

* `colmap-cams` → `ColmapCamsPreview` iframes
  `/colmaputil/index.html?embed=1` (vendored from
  `hololab/frontend/vendor/ColmapUtil`, build workflow in
  `hololab/frontend/vendor/HOLOLAB_VENDORED.md`), waits for its
  `colmap-ready` handshake, then posts a `colmap-load-files` message
  with `cameras.txt` + `images.txt` fetched from
  `${baseUrl}/{name}` plus a synthetic empty `points3D.txt` header
  (poses-only pack — ColmapUtil's loader mandates all three files but
  gracefully renders zero points).
* `colmap-points` → `ColmapPointsPreview` reuses the same
  `Colmap3DPreview` iframe/handshake plumbing with the *reverse*
  substitution: real `points3D.txt` from `${baseUrl}/`, synthetic
  empty `cameras.txt` + `images.txt` (points-only pack — the naked
  point cloud renders without camera frustums, which is the right
  shape for `colmap-triangulate`'s output). The scalar viewer is
  registered even though the current producer (`colmap-triangulate`
  with `arrayable: true`) only fans out; the arrayed fallback above
  gets the pager for free from that single scalar registration.

The iframe's sandbox is `allow-scripts allow-same-origin` since the
viewer needs JS and its own asset chunks resolve same-origin (via
Vite proxy in dev, SPA static mount in prod). No new gateway endpoint
required — the existing `/proxy/{node}/{sub}` route serves individual
files inside dir handles. See `ColmapUtil/README.md#嵌入模式` for the
message-type reference on the receiving side.

For the frontend to render the caret without a backend registry entry
(e.g. a rolling upgrade where the running gateway hasn't ingested a
new tag), `AlgorithmNode.expandableOutputs` promotes any port whose
tag is in `FRONTEND_VIEWER_TAGS` to `kind: "viewer"` even when
`spec.preview` is null. Keep that set in sync with the intercept
clauses at the top of `previews.tsx::Preview()` — the two together
form the tag-driven contract.

#### Basic-info fallback (no viewer + resolved handle)

Packs whose tags don't map to any viewer (typical for intermediate
utility outputs — `stg-train.model_dir`, `colmap-assemble.colmap`, …)
previously showed **no expand caret** on
the canvas node: the operator had to open the Artifacts panel to see
what got produced. As of this build the caret appears whenever the
pack declares `preview` **or** the latest run resolved a handle for
that port; the drawer body then swaps in `BasicInfoPreview`
(`previews.tsx`) — a compact card listing the producer's absolute
path, storage kind (`dir`/`file`), total size, entry count, and a
contents preview (top ~8 leaves, arrayed layouts flattened to
`<element>/<leaf>` rows). The header row is identical to a
viewer-backed drawer's — `pack · port · Open-in-Cocoder · CopyRef` —
so the actionable affordances (jump to on-disk dir, copy handle ref)
are always in the same place regardless of whether a bespoke viewer
was wired.

Placeholder rules from `f141ca4` are unchanged: `preview` declared
without a live handle still opens onto `PreviewPlaceholder` (never-ran
or cleaned) with the Run-this-node button; a port with neither
`preview` nor a resolved handle has no caret at all.

### Progress

```yaml
progress:
  stdout_regex: '\[ITER (\d+)/(\d+)\]'    # captures (current, total)
```

The node scans stdout for matches and forwards each as a `job_progress`
message.

## Idempotency and reruns

The node's execution sequence for a job:

1. Render all templates.
2. If `idempotency.marker` exists **and** `.hololab-metadata.json` next to it declares the same input handle set: re-register handles and mark job `done`.
3. Otherwise: create output directories, spawn subprocess, stream stdout/stderr, watch for cancel.
4. On exit code 0: check that all declared outputs exist as declared paths; register handles; write `.hololab-metadata.json`; write `.done` if declared.
5. On non-zero exit or cancel: emit `job_fail`.

## Versioning

`apiVersion: hololab.dev/v1` is the current schema. Breaking changes increment `v`.

`version:` on the pack itself is orthogonal semver for the pack's own code/behavior. Jobs record `(name, version)` at assignment; upgrading a pack does not affect running jobs.

## Legacy fields (accepted, ignored)

The following fields on port specs are recognized for backward compatibility
with older manifests but no longer participate in validation:

- `type: PathDir | PathFile | ...` on inputs and outputs
- `optional: true` on inputs (replaced by `required: false`)

New manifests should omit them.

## Authoring workflow

```bash
hololab pack init my-algo@0.1.0 --packs-dir ./packs
$EDITOR packs/my-algo@0.1.0/manifest.yaml
hololab pack validate packs/my-algo@0.1.0
```

The node's pack scanner picks up new packs and manifest edits automatically —
no restart needed.

## Multi-pack-source layout

The node scans **every entry in `pack_dirs`** (config.yaml — see
`NodeConfig.pack_dirs`, or edit from the UI's Pack sources field).
Each entry is polymorphic — the scanner picks the mode by what's on disk:

1. **A `.yaml` / `.yml` file** — that file is treated as a pack manifest.
   Lets one directory host multiple algorithms whose manifests sit
   alongside each other (e.g. `main.manifest.yaml`, `variant.manifest.yaml`).
2. **A directory containing `manifest.yaml`** — the directory *is* a pack
   (index.html-style default). Recommended for packs whose manifest is
   colocated with the algorithm's source code.
3. **A directory with no `manifest.yaml`** — each subdirectory is
   scanned for its own `manifest.yaml`. Preserves the historical
   `packs/<name>@<version>/` repository layout.

Pack identity (`name`, `version`) comes from the manifest content —
directory names are no longer required to match. Conflict resolution
across sources is **first-wins** + a warning. See
[`writing-a-pack.md`](writing-a-pack.md) for the operator-facing recipe.

## Full field reference

See `hololab/manifest/schema.py` for the pydantic models. This document
stays in sync with the schema; report drift as a PR.

### Default pinned parameters

A parameter may declare `pinned: true` to show its editor inside canvas nodes by
default. This is a strict boolean, defaults to `false`, and follows parameter
declaration order:

```yaml
params:
  iterations:
    type: int
    default: 1000
    pinned: true
```

Defaults apply only when `workflow.graph.pinned_params` has no explicit entry
for that exact `name@version`. An explicit empty list disables all defaults.
The first pin toggle copies the effective set into the workflow before applying
the change. Values remain per-node; only display configuration is shared.
New snapshots freeze the effective set, including empty sets. Existing snapshots
never pick up later manifest defaults. Removed parameters are not rendered, and
explicit configurations do not carry over between versions. Only actual Pack
parameters support this flag; package source and Fan-out controls do not.
Use `pinned`, not aliases such as `pin` or `default_pinned` (rejected by validation).
