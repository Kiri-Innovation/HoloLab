#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
export BIREFNET_WEIGHTS_DIR=${BIREFNET_WEIGHTS_DIR:-${HF_HOME:-$HOME/.hololab/node/weights/huggingface}/../birefnet}
# Conda dependencies may replace the declared pip CUDA pair. Restore the wheels
# after all declarative installs, without letting the resolver replace other deps.
"${PYTHON:-python}" -m pip install --no-deps --force-reinstall --index-url https://download.pytorch.org/whl/cu128 'torch==2.8.0' 'torchvision==0.23.0'
"${PYTHON:-python}" -m pip install 'fsspec[http]<=2025.3.0'
"${PYTHON:-python}" -c 'import torch, torchvision; assert torch.__version__.startswith("2.8.0"), torch.__version__; assert torchvision.__version__.startswith("0.23.0"), torchvision.__version__; assert torch.cuda.is_available(), "CUDA unavailable"'
"${PYTHON:-python}" -m pip check
"${PYTHON:-python}" "${PACKAGE_ROOT:-$(dirname "${BASH_SOURCE[0]}")}/weights.py"
