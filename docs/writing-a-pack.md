---
title: 编写 Pack
group: 接入算法
order: 10
---
# Writing a HoloLab pack

You have an algorithm in your own repo — a Python script, a shell one-liner
that shells out to a CUDA binary, whatever. You want it to appear as a node
in the HoloLab canvas so users can wire it up and dispatch jobs against your
compute node. This document is the recipe.

The contract is intentionally small: **a directory + one YAML file**. No
plugin registration, no Python-import hooks, no monkey-patching. HoloLab
never imports your code — the node runs your `exec.shell` in a subprocess
under the conda env you name.

## The 30-second version

```bash
# Recommended layout: manifest lives alongside the algorithm code.
$EDITOR /home/dev/my-algo/manifest.yaml
# register the source with your compute node:
#   * open the right-side "Compute nodes" panel → ⚙ → Pack sources
#     → add "/home/dev/my-algo" → Apply
# node rescans, the pack appears in the palette.
```

That's it. The rest of this document explains what goes in `manifest.yaml`
and why.

## Three ways to point at a pack

Each entry in the node's `pack_dirs` config is polymorphic — the scanner
picks the mode by what's on disk:

### 1. Directory with `manifest.yaml` (recommended)

```
/home/dev/my-algo/
├── manifest.yaml     ← the pack contract
└── main.py           ← your algorithm code, side by side
```

Add `/home/dev/my-algo` to `pack_dirs`. The scanner finds the top-level
`manifest.yaml` (index.html-style) and treats that directory as the pack.
`name` and `version` come from the manifest content — the directory name
is free-form.

**Prefer this**: the manifest lives with the code, so `exec.shell` can
reference sibling files with short relative paths, and the whole pack is
one self-contained subtree you can commit / rsync / delete as a unit.

### 2. Precise `.yaml` file — multiple algorithms in one folder

```
/home/dev/my-tools/
├── convert.manifest.yaml
├── convert.py
├── analyse.manifest.yaml
└── analyse.py
```

Add **two entries** to `pack_dirs`:

```yaml
pack_dirs:
  - /home/dev/my-tools/convert.manifest.yaml
  - /home/dev/my-tools/analyse.manifest.yaml
```

Each file is treated as one pack. Use this when several small algorithms
share a folder and giving each its own subdirectory would just add
ceremony.

### 3. Directory of pack subdirectories (legacy repo layout)

```
/opt/my-hololab-packs/
├── hello-world@0.1.0/
│   └── manifest.yaml
└── another-algo@2.1.0/
    └── manifest.yaml
```

Add `/opt/my-hololab-packs` to `pack_dirs`. The scanner iterates every
subdirectory that contains a `manifest.yaml`. Historical repository
layout used by the vendored HoloLab packs; supported unchanged.

### Cross-source conflict rule

If the same `name@version` appears in more than one `pack_dirs` entry,
the earliest listed entry keeps the pack — **first-wins**. Users extend
the vendored packs by *appending* their own source, not by shadowing.
Directory / file names never affect identity — `name` + `version` come
from the manifest content.

## Minimal `manifest.yaml`

```yaml
apiVersion: hololab.dev/v1
kind: Algorithm
name: hello-world
version: 0.1.0

description: "Prints greetings and writes a file."
category: "demo"
docs: |
  A minimal demo pack. Reads no input; writes a greeting into
  ``out_dir/hello.txt`` under the workspace root.

# What this node reads. Ports are typed by their ``tags`` — the object
# type. Wiring also checks collection structure and applicable axis labels.
# Storage controls transport. Omit ``inputs`` entirely for source packs.
inputs:
  seed:
    tags: [text]
    required: false
    description: "Optional greeting seed."

# Scalar knobs shown in the inspector. Distinct from ports; never tagged.
params:
  greeting:
    type: string
    default: "hello"
    description: "Word to print in the output file."

# What this node produces. At least one output is required.
outputs:
  out_dir:
    tags: [text]
    description: "Directory containing hello.txt."

# Runtime plumbing. ``env`` is a logical env name; the node maps it to
# a real conda prefix via ``node config.yaml → envs.<name>``.
runtime:
  env: kiri
  gpu:
    required: false
  expected_duration_min: [0.01, 0.5]
  resources:
    scratch_gb: 0.1
    mem_gb: 0.5

# The command template. ``{{ inputs.<port> }}``, ``{{ outputs.<port> }}``,
# ``{{ params.<name> }}``, and ``{{ scratch_dir }}`` are substituted by
# the node before the subprocess spawns.
exec:
  shell: |
    set -euo pipefail
    OUT="{{ outputs.out_dir }}"
    mkdir -p "$OUT"
    echo "[ITER 1/1] writing hello"
    echo "{{ params.greeting }} from hololab" > "$OUT/hello.txt"
    echo "done" > "$OUT/status.txt"

# Skip the exec if this marker already exists AND the input handle set
# matches — the "already ran with the same inputs" fast path.
idempotency:
  marker: "{{ outputs.out_dir }}/.hololab-done"

# Regex the node scans stdout for; captures (current, total). Keeps the
# ``ITER 1/N`` progress rings in the UI in sync.
progress:
  stdout_regex: '\[ITER (\d+)/(\d+)\]'

# Optional inline previews for the canvas node's preview drawer.
previews:
  - id: hello
    type: text
    path: "{{ outputs.out_dir }}/hello.txt"
```

Full field reference: [`pack-spec.md`](pack-spec.md).

### Making the ⌘/Ctrl+click "Jump to source" button useful

The canvas node header has a `</>` button. A plain click always opens
`manifest.yaml`. A ⌘/Ctrl+click opens the pack's core **implementation
script** when one is declared, or the manifest's directory otherwise.

To make ⌘/Ctrl+click jump straight to your main script, add `source_entry`
to the manifest — **relative paths resolve against the manifest.yaml's
own directory**:

```yaml
source_entry: main.py                    # sibling of manifest.yaml (recommended)
source_entry: subdir/main.py             # nested relative
source_entry: /opt/my-tools/main.py      # absolute
```

If your manifest is colocated with the code (mode 1 above), `source_entry:
main.py` is enough — no path juggling.

User-created packs that keep everything inside the pack directory can
skip `source_entry` — ⌘/Ctrl+click then opens the directory itself so
the operator sees all the files at once.

## Registering a pack source

Two ways to tell the node about a new pack source (see the three modes
above for the full picture — each entry can be a directory or a specific
`.yaml` file):

### From the UI (recommended)

1. Right-side **Compute nodes** panel → click the ⚙ on your node.
2. Under **Pack sources**, click **+ Add path**, paste an absolute path
   (a directory or a specific `.yaml` file), click **Apply**.
3. The node rescans immediately and pushes a `packs_updated` frame to
   the gateway; the palette refreshes without a restart.

### From `config.yaml`

Edit `~/.hololab/node/config.yaml`:

```yaml
pack_dirs:
  - /path/to/HoloLab/packs       # legacy repo layout
  - /home/dev/my-algo                              # colocated manifest+code
  - /home/dev/my-tools/convert.manifest.yaml       # precise-file
```

Then restart the node. The legacy `packs_dir:` scalar is still accepted
on load — the model validator promotes it to a single-entry `pack_dirs`
so existing configs keep working untouched.

## What can go wrong

- **Incompatible inputs.** Drag feedback reports a mismatched type,
  collection layer or axis label. Check the effective output and input
  contracts. A matching tag alone does not guarantee that their structures
  are compatible; the explicit `any` generic follows its own rule.
- **Path is not absolute.** `pack_dirs` entries must be absolute paths.
  `~/my-packs` in `config.yaml` gets expanded on load, but the API
  patch and CLI both reject relative paths outright.
- **`env: kiri` on a node that has no `envs.kiri`.** The node maps
  logical env names to conda prefixes in `config.yaml → envs`. Missing
  entry = the job fails at assignment with a clear message.
- **`resources` too optimistic.** The node preflights `scratch_gb`,
  `mem_gb`, and `gpu_mem_gb` against live disk/memory before it acks.
  Set these to the observed peak, not the average — a truthful estimate
  turns a mid-run SIGKILL into a fail-fast at assign time.
- **Manifest fails to load.** A YAML syntax error, a missing required
  field, or an unknown `apiVersion` gets logged as `pack manifest load
  failed` and the pack is dropped. Directory / file names never affect
  identity — `name` and `version` come from the manifest content, so
  renaming the directory won't help; open the log line and fix the
  manifest.
- **Two sources declaring the same pack.** The node keeps the first
  and warns about the second (`pack ignored — duplicate of an earlier
  source`). Reorder `pack_dirs` if you meant the *later* source to
  take precedence.

## Migrating an existing algorithm into HoloLab

If you own a repo with a working algorithm and want it to appear as a
node in HoloLab, this section is the checklist. The 30-second version
above tells you what to type; this one tells you what to *decide*
before you type it.

### 1. Where the manifest lives

Put `manifest.yaml` **inside your algorithm's repo**, next to the code
it wraps (mode 1 or 2 above). Two reasons:

- The manifest is versioned with the algorithm — a breaking change to
  a script and its corresponding manifest tweak land in the same
  commit.
- `exec.shell` can reference sibling files with short relative paths
  (explicit paths configured for the deployment).

The HoloLab repo's `packs/` directory is only for the source packs
that ship with HoloLab itself (video-source, demo, etc.). External
algorithms should never need to check anything in there.

### 2. How to slice one algorithm into packs

**One algorithm = one pack** is the default. A pack has a single
`exec.shell` block and produces one snapshot of outputs. If your
algorithm has clearly separable phases (SfM → training → export),
prefer one pack per phase and let users wire them on the canvas.

Signals that argue for splitting:

- One phase is CPU-only and quick; the next needs a 24 GiB GPU for
  hours. Splitting lets users retry the expensive step without redoing
  the cheap one.
- Users would reasonably want to pause between phases (inspect an
  intermediate output, adjust a param, resume).
- The intermediate artefact is a useful object on its own (e.g. a
  COLMAP directory that other tools consume).

Signals that argue for keeping it as one pack:

- The phases share a large in-memory state that would be expensive to
  serialize and reload.
- The intermediate isn't consumable by anything else and users would
  never inspect it.

### 3. Design your tags

Tags name the object type on a port. Leaf tags, collection structure and applicable axis-label rules together determine compatibility; explicit `any` provides generic matching. See [Pack ports](pack-spec.md#ports--tags-are-the-object-type).

- **Reuse an existing tag** when your output is genuinely the same
  object as an existing pipeline stage — `colmap`, `stg_model`,
  `splatv`, `video-source`. Look at `hololab/packs/` and the vendored
  official-pack manifests for the tag vocabulary in use.
- **Add a specialising tag** to an existing tag when yours is a
  subtype — e.g. `tags: [colmap, sharp4dgs_export]` means "a COLMAP
  directory that is also a sharp4dgs export." Downstream packs that
  read plain `[colmap]` still accept it; specialised consumers that
  need `[sharp4dgs_export]` can require it.
- **Invent a new tag** only when your object is genuinely new. Once
  chosen, it becomes a shared vocabulary token — future packs that
  consume it must use the same string.

If you want tag-driven default previews (splatv renderer, video grid),
add an entry to `hololab/gateway/tag_viewers.py::TAG_VIEWER_REGISTRY`;
every pack whose output carries that tag then gets the viewer for free.

### 4. Runtime coordination with the compute node

Two things need to line up with the node config on the machine that
will run the pack:

- **`runtime.env`** is a *logical* name. The node's `config.yaml` maps
  `envs.<name>` to a real conda prefix (e.g. `envs.kiri:
  /path/to/conda/envs/kiri`). Coordinate with the operator so the env
  they've provisioned matches the name in your manifest.
- **`runtime.resources`** — `scratch_gb`, `mem_gb`, `gpu_mem_gb`. The
  node runs a live preflight against `df` / `MemAvailable` before it
  acks a job; declaring a truthful *peak* turns a mid-run SIGKILL into
  a fail-fast at assign time. Measure once with a smoke run rather
  than guessing.

### 5. Wiring inputs, outputs, and scratch

- `{{ inputs.<port> }}` and `{{ outputs.<port> }}` render to absolute
  paths the node created for you. Write into `{{ outputs.<port> }}`;
  never write into `{{ inputs.<port> }}`.
- For temporary files that must NOT be part of the output, use
  `{{ scratch_dir }}`. Successful jobs schedule scratch and staging cleanup in
  the background. Failed-job data and failed cleanup attempts are left for the
  retention sweeper; do not assume immediate removal after every terminal state.
- Set `idempotency.marker` to a file *inside* your output. Write it
  last, from within `exec.shell`. On re-run with the same inputs the
  node skips the subprocess and re-registers the existing handles.

### 6. Validate end-to-end

```bash
# Local: manifest parses, required fields present.
hololab pack validate /path/to/your/pack

# On the compute node: add the source and rescan.
#   UI: Compute nodes → ⚙ → Pack sources → + Add path
#   or edit ~/.hololab/node/config.yaml → pack_dirs and restart the node.

# Optional HTTP inspection (set HOLO to your gateway URL first):
HOLO=http://127.0.0.1:8828
curl -s $HOLO/api/pack-catalog | jq '.[] | select(.name=="your-pack") | {name, version, manifest_path, source_dir}'

# Wire a workflow that uses your pack in the UI, dispatch a run, and
# tail its log if it fails.
curl -s "$HOLO/api/jobs?algorithm_name=your-pack&state=failed&limit=1" | jq .
curl -s "$HOLO/api/jobs/$JOB_ID/log?tail=200&stream=both" | jq -r '.lines[].line'
```

If the pack doesn't appear in `/api/pack-catalog`, look at the node
log for `pack manifest load failed` (your YAML is broken) or `pack
ignored — duplicate of an earlier source` (an earlier `pack_dirs`
entry already declared this `name@version` — reorder to change
precedence). If it appears but jobs fail immediately at assignment,
your `runtime.env` name isn't in the node's `envs:` map, or
`runtime.resources` exceeds what the node has free.

## Where does a new pack live?

Two homes, chosen by dependency:

- **HoloLab-owned (`hololab/builtin_packs/<name>@<version>/`)** — generic utility
  that works with anything downstream and has **zero deps on any specific
  algorithm repo** (only stdlib + widely-available libs like cv2 / numpy /
  ffmpeg). Ships with HoloLab, no algorithm checkout required. Examples
  today: ``builtin/single-video-source``, ``builtin/video-array-source``,
  ``builtin/frame-extraction``, ``demo-*``.
- **Algorithm-repo-owned (colocated with the algorithm code)** — pack that
  invokes algorithm-specific scripts and needs its checkout. Examples
  today: sharp-4dgs's ``frames-to-camera-track`` /
  ``track-to-gs-sequence`` / ``gs-seq-to-multiview-colmap`` /
  ``video-to-colmap`` / ``video-to-camera-track`` (deprecated combo);
  SpacetimeGaussians's ``stg-train``; STG_to_SplaTV's ``stg-to-splatv``.

Rule of thumb: if you can't `import` your entrypoint from a plain kiri
env without adding the algorithm repo to `sys.path`, it belongs next to
the algorithm. Otherwise it can (and probably should) ship with HoloLab.

## Keep examples portable

Place the manifest alongside the algorithm it wraps. Configure the actual
checkout path in the compute node's Pack sources. Use documented template
bindings and resolve script locations deliberately; do not copy another
machine's checkout or conda paths into a published manifest.

## Recommended node defaults

A pack can recommend `parallelism: 4` and `batch_size: 2` at the top level of
`manifest.yaml` (illustrative values; benchmark your pack before choosing them).
Each field is optional and must be at least 1; omitted or `null` leaves the
framework default of 1. The canvas copies the recommendations into explicit
workflow node values when the node is created. Existing nodes and subsequent
user edits keep their stored values: these are creation defaults, **not runtime
fallbacks or constraints**, and they do not enable `arrayed_toggle`. API clients
must copy the catalog values themselves; omitted API fields still become 1.

These fields are part of the pack's code identity. **Bump the pack version when
changing either recommendation**: changing the manifest changes its identity,
so old snapshots require the original pack content to replay. See
[the pack contract](pack-spec.md#arrayed-and-arrayable).

## Writing an arrayable pack

An **arrayable** pack tells the framework "my exec is data-parallel over
its arrayed inputs — dispatch one shard per element." When the operator
turns on the "并行处理数组输入" checkbox on a node instance of your pack,
the scheduler mints one parent coordinator job plus N shard jobs. Each
shard runs your exec against one element of the arrayed inputs; the
framework aggregates their outputs into an arrayed output handle
automatically.

Three things you must do:

1. **Declare `arrayable: true`** at the top level of your manifest. That
   surfaces the checkbox in the Inspector.
2. **Keep your exec strictly per-shard**. No shared state across shards,
   no coordination, no appending to shared files. The framework routes
   each shard's outputs into a distinct per-element path; two
   shards writing to the same path is a bug your pack owns.
3. **Declare exactly what one call consumes and produces**. A single image
   requires `tags: [image], storage: file`; an image collection requires
   `tags: [image], arrayed: true, storage: dir, dim_labels: [cam]` (or its actual
   outer-first axis labels). Fan-out adds one outer layer; it does not replace
   an existing array. Use `scalar: true` inputs to broadcast a declared value.

System responsibility: reject deliveries that violate declared shape and decode
all image leaves before execution / publication. Developer responsibility:
ensure declarations match algorithm expectations. The system does not inspect
algorithm intent. `image_sequence` / `frame_sequence` type tags are rejected;
port names are independent and may retain those names.

Available template bindings inside the shell for shard jobs:

```
{{ shard.element_id }}    — the element key this shard is processing
{{ shard.index }}         — 0-based ordinal (sorted element order)
```

Both are exposed only for shard runs; StrictUndefined turns a stray
reference on a non-shard run into a template error rather than a silent
mislabelling — safe to sprinkle only where they're actually needed.

Output layout your shell writes to (same `{{ outputs.<port> }}` template
you already use — the framework routes it):

```
{{ outputs.<port> }}      — resolves to
                            {parent_workspace}/<port>/{shard.element_id}/
```

For `storage: dir`, write under `{{ outputs.frames }}/...`; the runtime
pre-creates that directory. For `storage: file`, write the file at the exact
output path; only its parent directory is pre-created. The parent handle
points at `{parent_workspace}/<port>/`, containing one file or bundle per shard.

Failure model (parent success requires every shard):

- Any shard failure marks the parent FAILED with a fail_message naming
  the failing element. The bounded worker pool drains scheduled shards;
  downstream nodes do not consume a partially successful parent.
- Retries and partial-success recovery are follow-ups; if you need
  per-shard retry today, do it inside your shell.

Concrete example — an arrayable frame extractor:

```yaml
apiVersion: hololab.dev/v1
kind: Algorithm
name: builtin/frame-extraction
version: 0.1.0
arrayable: true

inputs:
  video_dir:
    tags: [video-source]              # arrayed=false in manifest
                                      # → flips to arrayed at toggle time
outputs:
  frame_sequence:
    tags: [image]
    arrayed: true                    # one call produces a frame collection
    storage: dir
    dim_labels: [frame]              # fan-out adds an outer camera axis
runtime:
  mode: host
exec:
  python: extract_frames.py
  args: ["--input-dir", "{{ inputs.video_dir }}", "--output", "{{ outputs.frame_sequence }}"]
```

When the operator turns off the checkbox, the same pack works as a
plain per-video extractor — no branching required.

## Where to look

- Schema — `hololab/manifest/schema.py::Manifest`
- Scanner — `hololab/node/packs.py::scan_packs`, `scan_multi_packs`
- Node config — `hololab/node/config.py::NodeConfig`
- Full pack spec — [`pack-spec.md`](pack-spec.md)
- Arrayed<T> type system — [`pack-spec.md`](pack-spec.md#arrayed-and-arrayable)
- Source and artifact location jumps require the Flops Cobrowser integration and configured device mapping.
