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
# type. Edge compatibility is tags-only (storage form is transport hint,
# never affects wiring). Omit ``inputs`` entirely for source packs.
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
  - /cloud/cloud-ssd1/Kiri4DGS/hololab/packs       # legacy repo layout
  - /home/dev/my-algo                              # colocated manifest+code
  - /home/dev/my-tools/convert.manifest.yaml       # precise-file
```

Then restart the node. The legacy `packs_dir:` scalar is still accepted
on load — the model validator promotes it to a single-entry `pack_dirs`
so existing configs keep working untouched.

## What can go wrong

- **Tag mismatch.** Ports connect only if their tags overlap. If your
  new pack outputs `tags: [gs_sequence]` and the downstream stage reads
  `tags: [colmap]`, the canvas edge silently refuses to draw. Look at
  upstream/downstream manifests when writing yours.
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
- **Directory-name mismatch.** `hello-world@0.1.0/` with a manifest
  saying `name: hello_world` is dropped silently (with a `pack
  directory name mismatches manifest` log). The scanner treats the
  directory name as the authoritative binding.
- **Two sources declaring the same pack.** The node keeps the first
  and warns about the second (`pack ignored — duplicate of an earlier
  source`). Reorder `pack_dirs` if you meant the *later* source to
  take precedence.

## Case study: Kiri4DGS's own official packs

The pipeline packs shipped with Kiri4DGS (Sharp-4DGS Phase A1/A2/A3, STG
training, .splatv export) follow a specific convention worth calling out —
you may want to mirror it for other in-repo algorithms.

**Layout — one manifest per algorithm, colocated with the code:**

```
Kiri4DGS/
├── sharp-4dgs/per-frame/
│   ├── video_to_colmap.py                       ← algorithm (A1)
│   ├── video-to-camera-track.manifest.yaml      ← pack for the above
│   ├── colmap_to_3d.py                          ← algorithm (A2)
│   ├── track-to-gs-sequence.manifest.yaml       ← pack for the above
│   ├── gsseq_to_multiview.py                    ← algorithm (A3)
│   ├── gs-seq-to-multiview-colmap.manifest.yaml ← pack for the above
│   ├── video_to_3d.py                           ← algorithm (A1+A2+A3 combo)
│   ├── video-to-colmap.manifest.yaml            ← pack for the above
│   ├── path_setup.py                            ← support / not a pack
│   └── ...
├── SpacetimeGaussians/
│   ├── train.py                                 ← algorithm
│   └── manifest.yaml                            ← pack (mode 2, index.html)
└── Utils/STG_to_SplaTV/
    ├── convert_to_splatv_lite.py                ← algorithm
    └── manifest.yaml                            ← pack (mode 2)
```

Four algorithms share `per-frame/` so each gets its own
`<name>.manifest.yaml` (**mode 1**, precise-file). `SpacetimeGaussians/`
and `Utils/STG_to_SplaTV/` are single-purpose directories so a top-level
`manifest.yaml` (**mode 2**, index.html) is clean.

**pack_dirs on the compute node** — one entry per manifest for mode-1,
one entry per directory for mode-2:

```yaml
pack_dirs:
  - /cloud/cloud-ssd1/Kiri4DGS/sharp-4dgs/per-frame/video-to-camera-track.manifest.yaml
  - /cloud/cloud-ssd1/Kiri4DGS/sharp-4dgs/per-frame/track-to-gs-sequence.manifest.yaml
  - /cloud/cloud-ssd1/Kiri4DGS/sharp-4dgs/per-frame/video-to-colmap.manifest.yaml
  - /cloud/cloud-ssd1/Kiri4DGS/sharp-4dgs/per-frame/gs-seq-to-multiview-colmap.manifest.yaml
  - /cloud/cloud-ssd1/Kiri4DGS/SpacetimeGaussians
  - /cloud/cloud-ssd1/Kiri4DGS/Utils/STG_to_SplaTV
  - /cloud/cloud-ssd1/Kiri4DGS/hololab/packs   # source / demo packs
```

**Absolute paths everywhere in the manifest** — this is the deliberate
convention for Kiri4DGS's own packs:

```yaml
source_entry: /cloud/cloud-ssd1/Kiri4DGS/sharp-4dgs/per-frame/video_to_colmap.py

exec:
  shell: |
    cd /cloud/cloud-ssd1/Kiri4DGS
    python /cloud/cloud-ssd1/Kiri4DGS/sharp-4dgs/per-frame/video_to_colmap.py "$VIDEO" ...
```

No `{{ pack_dir }}/../..` navigation, no `params.sharp_repo` /
`params.stg_repo` indirection. **The tradeoff is intentional**:

- ✅ **Explicit and unambiguous** — one glance at the manifest tells you
  exactly what file runs; no template resolution to trace through.
- ✅ **No user-facing "where is your repo?" parameter** to fill in per
  compute node.
- ❌ **Manifests are pinned to this specific machine's checkout path**
  (`/cloud/cloud-ssd1/Kiri4DGS`). If the repo moves, every manifest
  needs updating.

That's a fine tradeoff for **Kiri4DGS's own official packs**, which are
authored for the main experimental machine. Third-party or portable
packs should still prefer `{{ pack_dir }}` (or accept path params) —
the polymorphic pack_dirs contract supports both styles.

## Where to look

- Schema — `hololab/manifest/schema.py::Manifest`
- Scanner — `hololab/node/packs.py::scan_packs`, `scan_multi_packs`
- Node config — `hololab/node/config.py::NodeConfig`
- Full pack spec — [`pack-spec.md`](pack-spec.md)
- Cobrowser "Jump to source" — [`cobrowser-integration.md`](cobrowser-integration.md)
