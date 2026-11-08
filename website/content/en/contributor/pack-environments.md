---
title: Pack environments and dependencies
group: Collaboration and reproducibility
order: 145
---
# Pack environments and dependencies

Installing a pack makes its code available on a node. Preparing its environment makes that code executable. Treat these as separate operations, and declare dependencies so operators can check what is required.

## Install code, then prepare the environment
```bash
hololab pack install community/example@1.0.0
hololab pack setup community/example@1.0.0 --dry-run
hololab pack setup community/example@1.0.0
```

Replace the example identity with an existing exact registry version. Run on the execution node, using its configuration. `pack install` downloads and verifies the registry archive into `node/installed_packs/`; it does not create an algorithm environment or download model weights. The repository's `community_packs/` is a source archive, not the installed-pack directory.

`pack setup` finds the installed manifest, creates or reuses its conda environment, installs declared conda and Python packages, then runs its verified `setup.script` when needed. The dry run previews actions without creating environments or running hooks. Conda must already be available. Host-runtime packs are skipped by this conda setup path. Setup receipts allow unchanged successful hooks to be skipped; hooks should still be safe to rerun.

## Declare requirements in the manifest

This fragment illustrates the fields; it is not a complete manifest:
```yaml
runtime:
  env: image-tools
requires:
  python: ">=3.10,<3.13"
  conda_packages:
    - "conda-forge::ffmpeg=7.*"
  packages:
    - "Pillow==11.0.0"
setup:
  script: setup.sh
```

- `requires.python` accepts a PEP 440 version constraint or an exact version. Choose a supported interpreter version; the environment creator does not promise to reproduce an exact patch release.
- `requires.conda_packages` accepts `[channel::]name[=version[.*]]`, such as the example above. URLs, build strings and arbitrary command options are not supported.
- `requires.packages` uses Python **distribution names**, which can differ from import names, with optional PEP 440 constraints. URLs, extras and conditional markers are not accepted. Setup normally tries conda-forge then pip fallback; declared `package_indexes` can select a package-specific index.
- Declare native/build prerequisites as well as Python packages. Optional `cuda` describes minimum driver-supported CUDA, not an installed toolkit; GPU requirements do not promise currently free memory.

Use exact tested dependency versions where reproducibility matters. Keep declarations compatible for every pack intended to share an environment. A published manifest is immutable: changing requirements needs a new pack version. See [package identity](package-identity-registry.md).

## Logical names and physical environments

`runtime.env` is a logical name looked up in the node's `envs` mapping. Setup creates or discovers a conda environment with that name if no mapping exists, then records its prefix. Named environments live in conda's configured `envs_dirs`, normally its first writable directory, not inside the installed pack. Existing mappings are preserved; setup does not silently rebuild an existing environment or replace its interpreter.

Environments are separate from HoloLab's own application environment, but are **not necessarily separate per pack or version**. Packs with the same logical name share one environment. Different logical names may also map to the same physical prefix. `hololab pack setup --all` combines requirements by logical name and detects conflicting declarations within a group; it does not provide a lockfile or complete conflict isolation across aliases of one physical prefix. Use separate names and prefixes when dependencies need isolation.

## Download weights into node storage

Keep model weights out of Git and out of the published pack archive. Ship download logic in a package-relative `setup.script` and put downloaded resources in the node cache, by default `$HOLOLAB_HOME/node/weights/`, rather than mutating installed pack sources. Verify downloads against a known checksum and respect the model's license and access requirements.

Setup hooks and jobs receive the same node-configured `TORCH_HOME`, `HF_HOME`, and `HF_HUB_CACHE` values. Use these library cache locations so setup and execution reuse downloads. Node `cache_root` and per-library `cache_dirs` can override defaults; avoid hard-coded machine paths. Hooks also receive `PACKAGE_ROOT`, `CONDA_PREFIX`, and `PYTHON` for source access and the selected interpreter. Use the selected environment rather than the shell's unrelated Python. For custom download formats, make the hook and runtime reader agree on a stable location under the configured cache.

See [storage layout](../use/storage-layout.md) for ownership and cleanup. Moving artifacts does not move weights or environment prefixes.

## Check readiness and understand its limits

Use the canvas Pack Manager to inspect environment readiness or missing setup, and the workflow's local capability check to inspect declared requirements. Recheck after installing dependencies. Missing environments and version mismatches need correction on the selected execution node, not on the browser host. Checks inspect available metadata and declared capabilities; they do not install software.

A passing check covers only declared, probeable requirements at that time. It does not prove all imports work, native extensions have a compatible ABI, editable sources match, or a complete execution will succeed. Packs without requirements remain undeclared rather than gaining a reproducibility guarantee.

**Known boundary: dependency versions can drift.** Installing another pack into a shared physical environment, or installing dependencies in a setup hook, can change versions that previously satisfied a pack. The UI may then report a version mismatch. Pin tested versions, check all affected packs after installation, and use distinct prefixes for incompatible dependencies. Installing packs one at a time does not freeze the environment. Environment check evidence records a point in time; it is not an environment lock or part of the measured runtime's code hash.
