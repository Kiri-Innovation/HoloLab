# HoloLab Architecture

This document is the source of truth for HoloLab's design decisions. If code and this document disagree, one of them is wrong — file an issue.

## Three identities

HoloLab has exactly three moving parts:

| Identity     | Role                                                                        | Ships in artifact | Language     |
| ------------ | --------------------------------------------------------------------------- | ----------------- | ------------ |
| **Gateway**  | Control-plane backend. Serves frontend, brokers WS, keeps state, preview proxy. | Yes               | Python 3.10+ |
| **Node**     | Compute-node runtime. Scans packs, runs jobs as subprocesses, serves files. | Yes               | Python 3.10+ |
| **Frontend** | React canvas. Never talks to nodes directly; always through gateway.        | Yes (as static)   | React + TS   |

A single Python package `hololab` contains all three; the `hololab` CLI selects which subset(s) to run. The frontend is built (Vite) into static assets and shipped as package data.

## Connection topology

**Node → Gateway, outbound WebSocket only.** No inbound to nodes, ever.

Rationale:

- Real-world nodes sit behind NAT / corporate firewalls / cloud security groups. Outbound WS is the only connection direction that always works.
- One authentication direction (gateway authenticates incoming node tokens).
- Same code path for local (loopback), self-hosted, and hosted-gateway deployments.

Concrete rules:

- Node dials `ws://<gateway>/ws/node` on start.
- Handshake exchanges protocol versions (`v_min`, `v_max`) — see [Protocol](#protocol).
- Heartbeat every 15 s. Gateway declares node dead at 45 s without heartbeat.
- Reconnect uses exponential backoff (1 s → 2 s → 4 s → … capped at 30 s), forever.
- After reconnect, gateway **re-syncs** by asking the node which jobs it currently has running. The gateway's account is authoritative for anything else.
- Frontend connects to `ws://<gateway>/ws/frontend` — pure subscription channel.

For the "both parties are behind NAT" edge case (user's laptop as gateway, home GPU as node, both behind NAT), we plan an optional third-party rendezvous relay. **Not built yet.** Interim workaround: run the gateway on a host either side can reach.

## Protocol

All WebSocket messages share a single envelope:

```json
{
  "v": 1,
  "id": "uuid",
  "kind": "job_progress",
  "payload": { "...": "..." },
  "ts": 1735000000.0
}
```

- `v` — protocol version. Both sides declare `v_min`/`v_max` in the register frame; the intersection maximum is chosen.
- `id` — unique per message. Used for log correlation, not for RPC (we don't do request/response).
- `kind` — discriminated union tag; see `hololab/protocol/messages.py`.
- `payload` — kind-specific, validated by pydantic.
- `ts` — sender's wall-clock (monotonic sequence is not enforced across parties).

### Message kinds (MVP)

Node → Gateway:

- `register` — `{node_name, packs[], gpu_info, advertised_url, token, v_min, v_max}`
- `heartbeat` — `{running_jobs[], gpu_util?}`
- `job_ack`, `job_progress`, `job_log`, `job_done`, `job_fail`
- `handle_register` — node announces a new produced file/dir
- `handle_locate_req` — node asks gateway where to fetch a handle (future: cross-node)

Gateway → Node:

- `register_ok` — `{session_id, node_id}` (server-assigned)
- `register_err` — `{code, message}`
- `job_assign` — `{job_id, algorithm, version, params, input_handles, workspace_paths}`
- `job_cancel` — `{job_id}`
- `handle_locate_resp` — `{handle_id, node_id, url}`

Gateway → Frontend:

- `job_update` — full or partial job state
- `log_chunk` — batched log lines
- `preview_ready` — a preview asset is now downloadable via `/proxy/{node}/...`
- `node_online` / `node_offline`

`job_fail` payload carries `{reason, exit_code, log_tail}` where reason ∈ `user_error | algo_error | system_error | oom | cancelled`. This enables the scheduler to decide whether to auto-retry (only `system_error`) and the UI to color the failure.

We deliberately have **no bidirectional RPC framework**. Every request has a natural correlation ID (usually `job_id`); pattern matching on `kind` suffices.

## Persistence

SQLite from day 0, opened in WAL mode. All writes flow through a **single asyncio writer task**; reads may be concurrent. Rationale: aiosqlite's connection-per-task pattern under load can produce writer-writer contention on WAL; funneling writes through one queue is simpler than tuning.

Two central tables:

- `jobs` — current state (denormalized for quick UI queries).
- `job_events` — append-only. Every state transition is a row. The current state can be reconstructed from the event stream; the `jobs` table is a cache. This gives us **event replay for recovery** and a natural audit log.

Other tables: `nodes`, `packs`, `handles`, `workflows`, `snapshots`.

Schema migrations use a simple `schema_version` table and forward-only `hololab/persistence/migrations/NNN_*.sql` files. We picked this over Alembic because our schema is small enough not to need a full migration framework, and shipping raw `.sql` files is easier for contributors to read.

User data location:

- Linux / macOS: `$HOME/.hololab/` (or `$XDG_DATA_HOME/hololab/`)
- Windows: `%APPDATA%/hololab/`

Never inside the repo or the installed package.

## Workflow model

Two states:

- **Draft** — the frontend's editable graph. Persisted server-side as JSON at named workflow granularity. Autosave to draft is client-side; explicit `Save` publishes to gateway.
- **Snapshot** — frozen at the moment the user hits "Run". Immutable. Jobs bind to a snapshot ID, not to the draft. Later edits to the draft don't affect a running snapshot.

Runs are displayed as a list of snapshots. Each snapshot's job DAG is materialized once and never mutated.

## Data plane

**Large files never traverse the gateway.**

- Gateway maintains a **handle book**: `handle_id (uuid) → { node_id, path, kind_tag, size, sha256? }`.
- Node registers handles as they are produced.
- Consumers ask the gateway to `locate` a handle; gateway returns `{node_id, http_url}`; consumer node pulls directly via HTTP.
- Preview payloads (thumbnails, small images, log lines) may pass through the gateway's proxy for browser convenience (browser can't reach NAT-hidden nodes; the gateway reverse-proxies to the node's local HTTP server).

Workspace convention on each node:

```
{workspace_root}/
├── w/{workflow_id}/
│   ├── inputs/{handle_id}/          # materialized inputs pulled from other nodes
│   └── j/{job_id}/
│       └── {output_name}/           # each output is a directory
│           ├── .done                # idempotency marker
│           └── ...                  # actual output files
```

Idempotency: if `{output}/.done` exists **and** the input handle set matches what's recorded in `.done`, the job is skipped and existing handles are re-registered.

## Execution boundary (sacred)

The node runtime **never** imports algorithm code. Every algorithm runs as a subprocess:

```
conda run -p <env_prefix> <shell command from manifest>
```

Why this is non-negotiable:

- Isolates torch/CUDA versions per algorithm.
- One segfaulting algorithm kills one job, not the node.
- Node runtime dependencies stay light (see `pyproject.toml`) — critical for single-artifact distribution.
- Same-language coupling would tempt "just import for the trivial case" — a slippery slope back to ComfyUI's env hell.

Environment inheritance: node passes a **clean** `env={}` dict to `subprocess.Popen`, never mutating its own `os.environ`. `conda run` handles `LD_LIBRARY_PATH`, `CUDA_HOME`, etc. internally.

Cancellation:

- Linux/macOS: `SIGTERM` → 30 s grace → `SIGKILL`.
- Windows: `CREATE_NEW_PROCESS_GROUP` + `CTRL_BREAK_EVENT` — **deferred**, marked TODO in code.

## Preview channel

Browser reaches previews via `GET /proxy/{node_id}/{path}` on the gateway, which reverse-proxies to the node's local HTTP file server. Node's file server binds `127.0.0.1` by default; only the gateway (from the node's outbound WS session) is authorized to proxy to it.

This trades some gateway bandwidth for the "browser can always see previews even when node is NAT-hidden" property. Small thumbnails and log lines are the common case; large downloads should be rare and can eventually get a signed-URL bypass.

## Manifest and packs

Algorithm packs are the open-source contract. Detailed in [`pack-spec.md`](pack-spec.md).

Machine-specific concerns live in the **node config**, not in the manifest:

- `conda_bin` — explicit path to the `conda` binary (no PATH guessing).
- `envs` — map logical env name → real prefix, e.g. `kiri: /cloud/cloud-ssd1/envs/kiri`.
- `workspace_root`, `packs_dir`, `advertised_url`, gateway URL, token.

The manifest declares a logical env name (`runtime.env: kiri`); the node resolves it against its `envs` map at execution time. A pack is thus fully portable across hosts.

## Comparison with ComfyUI

|                     | ComfyUI                                | HoloLab                                   |
| ------------------- | -------------------------------------- | ----------------------------------------- |
| Execution           | Single process, in-memory tensors      | Subprocess per job, files on disk         |
| Environment         | One shared Python                      | Per-algorithm conda env                   |
| Distribution        | Single machine                         | Gateway + N nodes, WS-connected           |
| Cache               | Python object hash                     | Output-dir marker + input handle set      |
| Long jobs           | Awkward (blocks queue)                 | First-class (heartbeat, resume, cancel)   |
| Extension unit      | Python class (`INPUT_TYPES`, ...)      | Directory with `manifest.yaml` + code     |
| Primary target      | Diffusion / generative AI              | CV research (SfM, NeRF, 3DGS, eval)       |

Where we borrow: the declarative node UX, tag-based edge type checking, and the "check idempotency marker before running" pattern. Where we diverge: everything about execution.

## Distribution

We ship one self-contained tarball per platform. **The user installs nothing**
— they extract the archive and run `bin/hololab`.

- **Base interpreter**: [python-build-standalone](https://github.com/astral-sh/python-build-standalone)
  — statically-linked-ish CPython that runs on any recent Linux (glibc ≥ 2.17)
  or macOS without system dependencies. We drop it into the artifact and
  install the HoloLab wheel into it at build time.
- **Frontend**: `hololab/frontend/dist/` (built by Vite) is bundled inside the
  wheel via `[tool.hatch.build.targets.wheel.force-include]`. The gateway
  serves it at `/`.
- **Shebang trampoline**: pip generates console scripts with an absolute
  interpreter path baked in — invalid on the user's machine. Our build
  rewrites `bin/hololab` (and `bin/pip*`) to a portable `sh` trampoline that
  resolves the co-located `python` at run time. Template at
  `build/sh-trampoline.tmpl`.
- **The artifact is stateless**. User data — SQLite, node config, workspace,
  logs — lives under `~/.hololab/` (or `$XDG_DATA_HOME/hololab` / `%APPDATA%\hololab`),
  never inside the extracted tree. "Upgrade" = replace the tree, restart.
- **Build layout**: `build/build-<platform>.sh`. Cross-compilation is not
  supported; each platform builds on its own OS (GH Actions matrix or local).
- **Boundary rule (non-negotiable)**: the artifact ships **light deps only** —
  FastAPI, uvicorn, websockets, pydantic, aiosqlite, httpx, typer, jinja2,
  structlog, watchfiles, pyyaml. **No torch, no CUDA, no opencv**. Heavy
  compute stays in the user's existing conda environments and is invoked via
  `conda run -p`. This is what keeps the artifact ~50 MB instead of ~5 GB.

Acceptance for a build: `bin/hololab --version` on a fresh machine (Docker
container with no Python installed is the acid test).

## What is *not* here yet

Explicitly out of scope for the first slice:

- Multi-user authentication (single-user token only).
- Rendezvous relay for double-NAT.
- Automatic retry on system errors.
- Cross-node handle transport (single-node MVP).
- Windows signal handling (Linux/macOS only for smoke).
- Automatic updates.
- SHA-256 dedup on handles (async future work).
- Streaming/partial outputs.

Each of these has a design sketch in the roadmap; none are load-bearing for the first vertical slice.
