# Contributing to HoloLab

Thanks for considering a contribution. This document is the setup + workflow reference for people hacking on HoloLab itself (backend, frontend, packs, or docs).

## Prerequisites

- Python **3.10, 3.11, or 3.12** (3.12 recommended; CI tests all three)
- Node.js **18+**
- Optional: `make`, `git`, and any conda distribution if you want to run real algorithm packs

We deliberately avoid extra required tooling (no `uv`, no `pixi`, no `pyenv`) — plain `venv + pip` is enough. Use whatever you prefer on top.

## First-time setup from a clean clone

```bash
git clone https://github.com/hololab-dev/hololab
cd hololab

python3.12 -m venv .venv-runtime
.venv-runtime/bin/pip install --upgrade pip
.venv-runtime/bin/pip install -e ".[dev]"

# Frontend deps (only needed if you touch UI or want the bundled dist)
cd hololab/frontend && npm install && cd ../..
```

After that, `.venv-runtime/bin/hololab --version` should print the current version, and `make test` should pass.

## Directory layout

```
hololab/
├── hololab/                  # Python package
│   ├── protocol/             # Wire envelope + typed messages
│   ├── manifest/             # Pack manifest schema + Jinja rendering
│   ├── persistence/          # aiosqlite + WAL + migrations
│   ├── gateway/              # FastAPI app, WS endpoints, jobs, handles, hub, proxy
│   ├── node/                 # WS client, pack scanner, executor, file server
│   ├── frontend/             # React + xyflow (Vite)
│   └── cli.py                # typer entrypoint
├── packs/                    # Example algorithm packs
├── docs/                     # architecture.md, pack-spec.md
├── tests/                    # pytest suite
├── build/                    # PBS + venv bundle build scripts
└── .github/workflows/        # CI (ruff + pytest matrix)
```

## Daily dev loop

Three terminals — see `README.md` for the full quickstart. The short version:

```bash
# Terminal 1: backend with auto-reload
.venv-runtime/bin/hololab dev

# Terminal 2: frontend HMR
cd hololab/frontend && npm run dev

# Terminal 3: whatever you're testing (curl, git, etc.)
```

Backend Python edits reload automatically. Frontend edits hot-reload. Manifest edits (`packs/**/manifest.yaml`) trigger a node re-scan without restart.

## Backend tests

```bash
make test           # or: .venv-runtime/bin/pytest -q
make lint           # ruff check
make fmt            # ruff format
```

Tests live under `tests/`. Anything touching disk should use `tmp_path`. Anything involving asyncio uses `pytest-asyncio` (auto mode is already configured in `pyproject.toml`).

## Frontend

```bash
cd hololab/frontend
npm run dev          # HMR at :5173, proxies to the gateway
npm run build        # → hololab/frontend/dist/  (Python package bundles this)
```

## Authoring an algorithm pack end-to-end

1. **Scaffold** the pack:

   ```bash
   hololab pack init my-algo@0.1.0 --packs-dir ./packs
   ```

2. **Edit** `packs/my-algo@0.1.0/manifest.yaml`. Fill in `inputs`, `outputs`, `params`, `runtime.env`, and the `exec.shell` command template. See `docs/pack-spec.md` for the full field reference.

3. **Validate** — this catches schema errors before you run:

   ```bash
   hololab pack validate packs/my-algo@0.1.0
   ```

4. **Point your node at your packs dir** — either edit `~/.hololab/node/config.yaml` or symlink `packs/` into it.

5. **Run** — while `hololab dev` is up, POST to `/api/jobs/run`:

   ```bash
   curl -sS -X POST http://127.0.0.1:8828/api/jobs/run \
     -H 'Content-Type: application/json' \
     -d '{"algorithm_name":"my-algo","algorithm_version":"0.1.0","params":{...}}'
   ```

   The node re-scans the packs dir automatically when you edit the manifest — no restart needed.

## E2E smoke

`demo-echo@0.1.0` is a portable pack that always succeeds. Full path:

```bash
make smoke
# or manually:
curl -sS -X POST http://127.0.0.1:8828/api/jobs/run \
  -H 'Content-Type: application/json' \
  -d '{"algorithm_name":"demo-echo","algorithm_version":"0.1.0","params":{"iterations":5}}'

# Check the outcome
curl -s http://127.0.0.1:8828/api/jobs | python3 -m json.tool | head
```

Success looks like `"state": "done"`, an output handle in the DB, and a workspace dir under `~/.hololab/node/workspace/w/<workflow>/j/<job>/`.

## E2E test hygiene

Ad-hoc E2E verification (poking the running gateway from the shell, from
a browser session, or from an integration test) has a strict discipline
so it stops polluting the Gallery with `e2e v2 / v3 / fresh` leftovers:

1. **One workflow name, reused.** Test workflows MUST use the reserved
   name `cc-e2e-test`. Fetch-or-create it — never mint per-session
   variants like `e2e v2` or `full chain fresh`.
2. **Session teardown.** When your verification session is done, delete
   the workflow row from the gateway. Job workspaces on disk can stay
   (they're immutable, easy to inspect later), but the workflow row
   itself must not linger in the Gallery.

   ```bash
   WF=$(curl -s http://127.0.0.1:8828/api/workflows \
        | python3 -c "import sys,json; \
          print(next((w['workflow_id'] for w in json.load(sys.stdin) \
                      if w['name']=='cc-e2e-test'), ''))")
   [ -n "$WF" ] && curl -s -X DELETE "http://127.0.0.1:8828/api/workflows/$WF"
   ```
3. **Integration tests do the same.** Tests under `tests/` that create
   workflows against a *shared* gateway (i.e. not `create_app(db_path=tmp_path/…)`)
   MUST clean up in a `finally:` / `tearDown` / pytest fixture teardown.
   Tests using per-test `tmp_path` databases (the common case in this
   repo — see `tests/test_run_history_api.py`) are exempt because their
   DB is thrown away with the tmp dir.

## Commit style

Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`, `refactor:`, `ci:`), small and self-contained. **Do not include AI-attribution footers** (`Co-Authored-By: Claude/GPT/...`) — house rule.

## Before opening a PR

- `make lint fmt test` all green
- New features have tests
- New public API has docstrings
- Behavior changes update `docs/architecture.md` or `docs/pack-spec.md`
- No absolute machine paths in library code (config, tests, and doc examples are fine)

## Filing issues / asking questions

- Bugs → GitHub Issues, please include reproducer + `~/.hololab/gateway/hololab.sqlite` snippet if state-related
- Design questions → GitHub Discussions
- Security issues → email in `SECURITY.md` (TODO)
