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

The directory name convention `NAME@VERSION` is enforced by the node's pack scanner. Multiple versions of the same pack coexist without interference; a job always locks the version at assignment time.

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
name the object type the port carries. Tags are the only thing edge
compatibility looks at: two ports are compatible iff their tag sets have a
non-empty intersection.

A port can carry multiple tags at once. Semantically this reads as "this
thing is BOTH an X AND a Y". A specialized producer might declare
`tags: [colmap, sharp4dgs_export]` — meaning it produces a colmap directory
that is specifically a sharp4dgs export. Any consumer that wants `[colmap]`
or `[sharp4dgs_export]` (or both) accepts it.

Ports carry NO other type information. `PathDir`/`PathFile`/etc. are not
part of the port spec — how the object is stored on disk is a system detail.
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

`storage` is **not** part of edge compatibility. Two ports with matching
tags are always compatible, regardless of storage form.

### `arrayed` and `arrayable`

An `arrayed<T>` is *a directory whose immediate subdirectories are elements
of type T*. Element ids are the sorted subdirectory names. Two independent
flags control whether a port carries one:

- Per-port **`arrayed: <bool>`** — the manifest declares the port's default
  cardinality. Example: `video-array-source.videos_dir` is intrinsically
  arrayed (`tags: [video-source], arrayed: true`).
- Per-pack **`arrayable: <bool>`** — the pack's exec is data-parallel over
  its arrayed inputs. When true the canvas shows a "并行处理数组输入"
  checkbox on each instance of the pack; when the operator turns it on,
  every port that manifest-defaults to non-arrayed is treated as arrayed
  and the scheduler fan-outs one sub-job (a *shard*) per element.

Edge compatibility is `tags overlap AND arrayed cardinality matches`. No
implicit scalar↔array broadcasting — wire an `arrayfy` node when you need
to promote a scalar to an array.

Shard mechanics (v1: sequential; parallelism knob is a follow-up):

- Framework mints one parent job (coordinator, no compute-node dispatch)
  plus N shard jobs, one per element of the arrayed inputs.
- Each shard writes its outputs to `{parent_workspace}/{port}/{element_id}/`
  instead of its own workspace (via a `shard_output_prefix` on `JobAssign`).
  Aggregation is automatic: after all shards finish, the framework registers
  one parent output handle per port pointing at `{parent_workspace}/{port}/`.
- Failure is all-or-nothing: any shard failure marks the parent FAILED with
  a fail message naming the element id and shard index, and the scheduler
  does not create the remaining shards.

The pack shell may reference `{{ shard.element_id }}` and `{{ shard.index }}`
when executing as a shard; these expand to empty for non-shard runs (Jinja2
StrictUndefined turns a stray reference into a loud error, so a template that
accidentally uses `shard.*` on a non-shard run fails fast).

The `arrayable: true` contract obligates the pack author to promise **no
cross-shard state or data exchange** — the framework has no way to enforce
this (shell can do anything), but violating it produces subtle bugs.

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

### Params — scalar knobs, distinct from ports

Params are typed scalars — `int`, `float`, `bool`, `string`, `enum`. They
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
- `job_id`, `workflow_id` — for logging purposes.

The rendered string is passed as a single command to `conda run -p <prefix> bash -c "<rendered>"`. Environment variables of the node process are not inherited; declare any you need inside the shell block.

### Idempotency

```yaml
idempotency:
  marker: "{{ outputs.result_dir }}/.done"    # optional path
```

If the marker exists after templating, the node skips the subprocess and
re-registers the existing output handles. The marker file should be written
by your command as the last step.

### Previews

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
(e.g. one produces `arrayed<frame_sequence>` by regrouping, the other
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

**Frontend viewer overrides.** A tag can also drive a richer *frontend*
viewer than the registry's declared choice. `frame_sequence` is the
canonical example: the registry pins it to the plain `image` viewer
(showing only `frames/frame_000000.png`), but when the frontend sees
that tag on a dir-storage handle it swaps in the stacked-strip viewer
(`FrameStripPreview` in `previews.tsx`) — a fanned overlap of ~6 sample
thumbs plus a `count · WxH` metadata line. The sample indices are
evenly spaced across the full sequence so the tail is always visible;
the last card overlays a `+N` badge when the run has more frames than
cards. Clicking a card opens the raw PNG in a zoomable overlay
(Esc/← to close). Adopt the same pattern for any tag whose "one entry
is representative" default isn't true.

**Arrayed viewers.** A tag family can register a *pair* of viewers —
one for the scalar `T`, one for `arrayed<T>` — since the arrayed form
adds an outer directory layer (`<parent>/<element>/<file>`) that the
scalar viewer wouldn't handle. `frame_sequence` is the canonical pair:

* scalar `frame_sequence` → `FrameStripPreview` (probes
  `frames/frame_XXXXXX.png`);
* `arrayed<frame_sequence>` → `NestedFrameSequencePreview` — a
  two-level fan showing up to 3 outer group cards (each a mini
  fanned stack of the group's first 3 thumbnails + a corner badge for
  the group's total image count) with a `共 M 组` label; click a group
  card to drill down into that group as a `FrameStripPreview`-style
  strip.

The dispatch key is the port's effective `arrayed` state — computed
per-node as `port.arrayed || (pack.arrayable && node.arrayed_toggle)`.
Both `frame-extraction[arrayed_toggle=true]` (per-camera groups) and
`regroup-by-frame` (per-frame groups) resolve to `arrayed<
frame_sequence>` and automatically share the nested viewer with no
per-pack preview declaration.

Data path for `NestedFrameSequencePreview`: a single
`GET /api/handles/{id}/summary`. The server-side enrichment
(`handle_summary._list_dir_children`) attaches immediate `children`
to each directory entry so the frontend gets the per-group image list
without an extra listing round-trip. Only ~9 thumbnails (3 groups × 3
thumbs) are loaded up front; the drill-down loads at most
`STRIP_MAX_CARDS` more. No probes.

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
