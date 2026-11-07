#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
export BIREFNET_WEIGHTS_DIR=${BIREFNET_WEIGHTS_DIR:-${HF_HOME:-$HOME/.hololab/node/weights/huggingface}/../birefnet}
"${PYTHON:-python}" "${PACKAGE_ROOT:-$(dirname "${BASH_SOURCE[0]}")}/weights.py"
