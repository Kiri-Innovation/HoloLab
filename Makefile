# HoloLab developer entrypoints.
#
# All commands assume the dev venv is at ``.venv-runtime`` (see CONTRIBUTING.md).
# Override with ``make VENV=/path/to/other/venv <target>``.

VENV ?= .venv-runtime
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip
HOLO := $(VENV)/bin/hololab

FRONTEND_DIR := hololab/frontend
GATEWAY_URL  ?= http://127.0.0.1:8828

.PHONY: help
help:
	@awk 'BEGIN {FS=":.*##"; printf "\nUsage: make \033[36m<target>\033[0m\n\n"} \
	  /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

.PHONY: install
install: ## Install the Python package in editable mode with dev extras.
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"

.PHONY: start
start: ## One-command product mode: gateway + embedded node, serves the built frontend.
	$(HOLO) start

.PHONY: dev
dev: ## Dev loop: gateway with auto-reload + node subprocess; run `npm run dev` in another shell.
	$(HOLO) dev

.PHONY: backend
backend: start ## Alias for `make start` — kept for muscle memory.

.PHONY: gateway
gateway: ## Start gateway only, with reload.
	$(HOLO) start gateway --reload

.PHONY: node
node: ## Start node only, dialing the local gateway.
	$(HOLO) start node --gateway ws://127.0.0.1:8828

.PHONY: frontend
frontend: ## Start Vite dev server (HMR).
	cd $(FRONTEND_DIR) && npm run dev

.PHONY: build-frontend
build-frontend: ## Build the frontend bundle into hololab/frontend/dist/.
	cd $(FRONTEND_DIR) && npm install && npm run build

.PHONY: test
test: ## Run the pytest suite.
	$(VENV)/bin/pytest -q

.PHONY: lint
lint: ## Run ruff lint.
	$(VENV)/bin/ruff check .

.PHONY: fmt
fmt: ## Run ruff format.
	$(VENV)/bin/ruff format .

.PHONY: fmt-check
fmt-check: ## Verify formatting without changing files.
	$(VENV)/bin/ruff format --check .

.PHONY: check
check: lint fmt-check test ## The full CI-equivalent gate.

.PHONY: smoke
smoke: ## POST the demo-echo pack and print the resulting job.
	@JOB=$$(curl -sS -X POST $(GATEWAY_URL)/api/jobs/run \
	    -H 'Content-Type: application/json' \
	    -d '{"algorithm_name":"demo-echo","algorithm_version":"0.1.0","params":{"iterations":5}}' \
	    | $(PY) -c 'import sys, json; print(json.load(sys.stdin)["job_id"])'); \
	  echo "job_id=$$JOB"; sleep 2; \
	  curl -s $(GATEWAY_URL)/api/jobs/$$JOB | $(PY) -m json.tool

.PHONY: clean
clean: ## Remove build/test caches.
	rm -rf build/ dist/ *.egg-info .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
