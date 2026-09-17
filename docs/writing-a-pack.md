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
mkdir -p /home/dev/my-hololab-packs/hello-world@0.1.0
$EDITOR /home/dev/my-hololab-packs/hello-world@0.1.0/manifest.yaml
# register the source dir with your compute node:
#   * open the right-side "Compute nodes" panel → ⚙ → Pack directories
#     → add "/home/dev/my-hololab-packs" → Apply
# node rescans, "hello-world" appears in the palette.
```

That's it. The rest of this document explains what goes in `manifest.yaml`
and why.

## Directory shape

```
/home/dev/my-hololab-packs/
├── hello-world@0.1.0/
│   ├── manifest.yaml     ← required — this is the pack contract
│   └── run.sh            ← optional — any files your exec references
└── another-algo@2.1.0/
    └── manifest.yaml
```

Rules the node's scanner enforces (see `hololab/node/packs.py`):

- Directory name must be `<name>@<version>` where `name` matches
  `[a-z0-9_-]+` and `version` is semver `MAJOR.MINOR.PATCH`.
- The directory name segments **must match the `name` / `version` fields
  inside `manifest.yaml`** — mismatch = the pack is skipped with a
  warning. This is the safety net against accidental version drift.
- If two directories in different pack sources declare the same
  `name@version`, the node keeps the one from the earliest-listed
  source (**first-wins**) and warns about the ignored duplicate. Users
  extend the vendored packs by *appending* their own source — not by
  shadowing.

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

## Registering a pack source

Any directory with `<name>@<version>/manifest.yaml` subdirectories is a
"pack source". The node scans one or more of them, in order. Two ways to
tell it about a new one:

### From the UI (recommended)

1. Right-side **Compute nodes** panel → click the ⚙ on your node.
2. Under **Pack directories**, click **+ Add legacy root** (same widget
   is used for both — the label re-purposes it), paste the absolute path,
   click **Apply**.
3. The node rescans immediately and pushes a `packs_updated` frame to
   the gateway; the palette refreshes without a restart.

### From `config.yaml`

Edit `~/.hololab/node/config.yaml` (or whatever path you pass to
`hololab start --config`):

```yaml
pack_dirs:
  - /cloud/cloud-ssd1/Kiri4DGS/hololab/packs   # the vendored packs
  - /home/dev/my-hololab-packs                 # your source
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

## Where to look

- Schema — `hololab/manifest/schema.py::Manifest`
- Scanner — `hololab/node/packs.py::scan_packs`, `scan_multi_packs`
- Node config — `hololab/node/config.py::NodeConfig`
- Full pack spec — [`pack-spec.md`](pack-spec.md)
- Cobrowser "Jump to source" — [`cobrowser-integration.md`](cobrowser-integration.md)
