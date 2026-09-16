# HoloLab

**A node-graph GUI for CV research pipelines.** Long-running GPU jobs, per-node previews, distributed by design.

HoloLab lets you compose computer-vision research pipelines (SfM, NeRF/3DGS training, novel-view generation, evaluation) as an interactive node graph. Each node is a real algorithm running in its own conda environment as a subprocess — no dependency conflicts, no in-process torch imports, no single-machine bottleneck.

> **Status:** day-0 skeleton. First vertical slice wraps SpacetimeGaussians training as an algorithm pack. Public API is not stable yet.

## Why not just ComfyUI?

ComfyUI is a single-process, tensor-in-memory engine built for diffusion inference. HoloLab targets the opposite regime: **subprocess-isolated, multi-conda-env, long-running (hours), file-in/file-out** jobs. See [`docs/architecture.md`](docs/architecture.md#comparison-with-comfyui) for the full comparison.

---

## Quick start (one command)

```bash
hololab start
# ─────────────────────────────────────────────
#   HoloLab
#   ↳ open http://127.0.0.1:8828
#   ↳ open http://<lan-ip>:8828
#   frontend    hololab/frontend/dist
#   workspace   ~/.hololab/
#   press Ctrl+C to stop
# ─────────────────────────────────────────────
```

That's the whole product experience — one process (gateway + a local node
in the same asyncio loop), one Ctrl+C to stop everything. The default
bind is `0.0.0.0` so you can open the UI from another machine on your
LAN. Pass `--host 127.0.0.1` to keep it loopback-only, or `--port N` to
move off `8828`. `hololab status` gives a one-liner sanity check ("is
the gateway up, which node is online, what did it last run?").

User data (SQLite, workspaces, node config) lives under `~/.hololab/`.
Uninstall is `rm -rf ./hololab/ ~/.hololab/`.

> **Distribution artifacts** (self-contained tarballs, no Python install
> needed) aren't published yet — follow the [Install from source](#install-from-source)
> steps below in the meantime.

## Install from source

Requires **Python 3.10+**, **Node.js 18+**, and either **conda** or **miniconda/mamba** if you plan to run packs that need it. Everything else is `pip`.

```bash
git clone https://github.com/hololab-dev/hololab
cd hololab

python3.12 -m venv .venv-runtime
.venv-runtime/bin/pip install -e ".[dev]"

# frontend deps + one-off build (dist is served by `hololab start`)
cd hololab/frontend && npm install && npm run build && cd ../..

.venv-runtime/bin/hololab start
```

We deliberately pick plain `venv + pip` for dev to avoid another required tool. If you like `uv`, it works fine too — nothing in the project cares.

---

## Develop (dev loop with HMR)

The one-command `hololab start` above is the *product* path. For active
development you want gateway auto-reload + Vite HMR — that's the
`hololab dev` split-process mode:

```bash
# 1. backend: gateway with auto-reload + local node subprocess
.venv-runtime/bin/hololab dev

# 2. frontend: Vite dev server with HMR
cd hololab/frontend && npm run dev
# → http://127.0.0.1:5173  (Vite proxies /api /proxy /ws → gateway on :8828)

# 3. smoke a job
curl -sS -X POST http://127.0.0.1:8828/api/jobs/run \
  -H 'Content-Type: application/json' \
  -d '{"algorithm_name":"demo-echo","algorithm_version":"0.1.0","params":{"iterations":5}}'
```

Edit `hololab/**/*.py` → gateway auto-reloads (uvicorn watches the package). Edit a pack's `manifest.yaml` → node re-scans and re-registers with the gateway. Edit `hololab/frontend/src/**` → Vite HMR.

`hololab dev` sets `HOLOLAB_DEV=1` for the gateway so it skips serving a stale `frontend/dist/` at `:8828` — always open the Vite URL (`:5173`) instead.

### Distributed / manual mode

`hololab start gateway` and `hololab start node` still exist for
multi-machine setups: one host runs the gateway, other hosts run
nodes that dial `ws://<gateway>:8828/ws/node`. See
[`docs/architecture.md`](docs/architecture.md#connection-topology).

### Node config

Machine-specific settings (conda binary path, env prefix map, ports) live in `~/.hololab/node/config.yaml`. Minimal example:

```yaml
node_name: my-workstation
gateway_url: ws://127.0.0.1:8828
conda_bin: /path/to/conda
envs:
  kiri: /path/to/conda/envs/kiri
```

### Common tasks

```bash
make test          # ruff + pytest
make lint          # ruff check
make fmt           # ruff format
make build-frontend
make smoke         # POST demo-echo and print the result
```

Or without `make`, the equivalents live in the [Makefile](Makefile).

---

## Architecture at a glance

```
┌─────────────┐  outbound WebSocket (persistent)         ┌─────────────┐
│    Node     │ ───────────────────────────────────────► │   Gateway   │
│  (GPU host) │ ◄─────────────────────────────────────── │  (control)  │
│             │        heartbeats, jobs, logs            │             │
│ • pack scan │                                          │ • REST/WS   │
│ • subprocess│    HTTP direct (large files)             │ • SQLite    │
│ • file srv  │ ────────────────────► other nodes        │ • proxy     │
└─────────────┘  (never through the gateway)             └──────┬──────┘
                                                                │
                                                        WebSocket + HTTP
                                                                │
                                                                ▼
                                                        ┌─────────────┐
                                                        │  Frontend   │
                                                        │ (React +    │
                                                        │  xyflow)    │
                                                        └─────────────┘
```

Full details in [`docs/architecture.md`](docs/architecture.md). Pack authoring spec in [`docs/pack-spec.md`](docs/pack-spec.md).

## Writing an algorithm pack

```bash
hololab pack init my-algo@0.1.0 --packs-dir ./packs
# edit packs/my-algo@0.1.0/manifest.yaml
hololab pack validate packs/my-algo@0.1.0
```

Every pack is a directory with a `manifest.yaml` declaring inputs, outputs, params, runtime env, and a shell command template. The node never imports your code — it runs your command as a subprocess in the conda env you declare.

## License

Apache-2.0. See [`LICENSE`](LICENSE).
