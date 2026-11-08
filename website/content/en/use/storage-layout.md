---
title: Where HoloLab keeps its files
group: Full Features Intro
order: 52
---
# Where HoloLab keeps its files

HoloLab keeps user data outside its installed application. `HOLOLAB_HOME` overrides the data home. Without it, Unix uses `$XDG_DATA_HOME/hololab` when configured, otherwise `~/.hololab`; Windows uses `%APPDATA%/hololab` when available. Below, `$HOLOLAB_HOME` means that resolved data home. Gateway and execution nodes on separate machines each have their own local data home.

## Default directory map
```text
$HOLOLAB_HOME/
├── gateway/
│   └── hololab.sqlite
└── node/
    ├── config.yaml
    ├── weights/
    ├── artifacts/
    └── installed_packs/
```

These are defaults, not a requirement to keep everything on one disk. Node configuration can select a different `workspace_root`, `cache_root` or per-library `cache_dirs`, pack scan directories, and environment mappings.

| Location | Who writes it / who reads it | Can it be deleted? |
| --- | --- | --- |
| `node/weights/` | Setup hooks and model libraries download shared resources; setup and jobs read the cache | Only while idle, and only if the resources can be downloaded again. Offline or manually supplied weights may be irreplaceable; preserve those separately. |
| `node/artifacts/` | Jobs write outputs; downstream jobs, file service and previews read them | Not a disposable cache. Use artifact cleanup with dependency/reference checks; deleting files manually leaves stale records and broken history. |
| `node/installed_packs/` | `pack install` extracts verified registry archives; scanning, setup and execution read them | Remove only when no required workflow/run depends on them. Reinstall the exact published version if needed; do not edit verified installed sources in place. |
| `gateway/hololab.sqlite` | Gateway owns the authoritative ledger and uses its database writer; maintenance tools use the supported database write path | Never treat it as a cache. Back up consistently, including SQLite WAL state when applicable; do not copy only the live main file or edit rows by hand. |
| `node/config.yaml` | CLI and configuration APIs persist node settings; node startup and runtime read them | Preserve it: it records identity, paths and environment mappings. Back it up before moving hosts. |
| Conda environment prefixes | `pack setup` and environment tools install dependencies; jobs launch interpreters here | Rebuildable only from adequate requirements and available sources. Do not remove active or shared environments; record mappings first. |

Repository `community_packs/` holds pack sources; `installed_packs/` holds local copies of published archives. The corresponding published code should match, but editing a checkout does not update an installed copy. Model files and mutable setup receipts are not pack source code.

## Environments live outside the artifact tree

Conda normally creates named environments in its configured environment directories (typically the first writable entry in `envs_dirs`); inspect `conda info --json` for the actual configuration. HoloLab records logical environment names under the node's `envs` mapping to physical prefixes. Existing mappings may point elsewhere. Moving the artifact directory does not move these prefixes. See [Pack environments and dependencies](../contributor/pack-environments.md).

## What workspace_root means

`workspace_root` is the node's primary output root and the primary namespace used by its file service. It defaults to `node/artifacts/`; it is not the repository checkout, the pack source root, or the weights cache. `legacy_workspace_roots` exposes additional historical roots so already registered paths remain readable. Adding an entry alone does not copy data or redirect future outputs.

For a whole-workspace [artifact migration](migrating-artifacts.md) connected to Gateway, the tool updates the primary root and retains legacy roots. Handle/path migrations leave the primary root unchanged. The old source path becomes a symlink to the new location, allowing historical absolute paths to resolve without rewriting every saved workflow. The old path's parent must remain available; adding a legacy root cannot repair a missing mount or broken symlink.

The migration service listens on loopback port 8830 and operates on the ledger and local storage. It can stop after maintenance; normal jobs and symlinks do not depend on it. Retained source copies support rollback only until explicit source reclamation.

## Back up by responsibility

Preserve the ledger, node configuration and needed artifacts together. Keep exact pack identities and environment mappings so code can be reinstalled and environments rebuilt. Weights are shared resources rather than per-run outputs: retain them if downloads are costly, unavailable or licensed separately. Changing `HOLOLAB_HOME` alone neither migrates files nor rewrites paths stored in configuration and history.
