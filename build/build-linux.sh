#!/usr/bin/env bash
# Build a self-contained linux-x64 HoloLab artifact.
#
# Strategy: python-build-standalone (pbs) provides a portable CPython that
# runs on any glibc >=2.17 Linux without system dependencies. We drop it into
# ``dist/hololab/``, pip install the package into it, rewrite the console
# script shebangs to a portable sh trampoline, and tar the whole tree.
#
# See build/README.md for the acceptance test.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_DIR="${REPO_ROOT}/build"
DIST_DIR="${REPO_ROOT}/dist"
STAGING="${DIST_DIR}/hololab"

PYTHON_VERSION="${HOLOLAB_PYTHON_VERSION:-3.12.7}"
PBS_TAG="${HOLOLAB_PBS_TAG:-20241016}"
PBS_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}/cpython-${PYTHON_VERSION}+${PBS_TAG}-x86_64-unknown-linux-gnu-install_only.tar.gz"

TRAMPOLINE="${BUILD_DIR}/sh-trampoline.tmpl"

log() { printf '[build-linux] %s\n' "$*" >&2; }

# -----------------------------------------------------------------------------
# 0. Precondition checks
# -----------------------------------------------------------------------------

if [ ! -d "${REPO_ROOT}/hololab/frontend/dist" ] || \
   [ ! -f "${REPO_ROOT}/hololab/frontend/dist/index.html" ]; then
  log "frontend not built; running 'make build-frontend' now"
  make -C "${REPO_ROOT}" build-frontend
fi

if [ ! -f "${TRAMPOLINE}" ]; then
  log "missing trampoline template at ${TRAMPOLINE}"; exit 1
fi

# -----------------------------------------------------------------------------
# 1. Fetch pbs, unpack into staging dir
# -----------------------------------------------------------------------------

rm -rf "${STAGING}"
mkdir -p "${STAGING}"

TMP_TGZ="$(mktemp --suffix=.tar.gz)"
trap 'rm -f "${TMP_TGZ}"' EXIT

log "fetching python-build-standalone ${PYTHON_VERSION}+${PBS_TAG}"
curl -Ls "${PBS_URL}" -o "${TMP_TGZ}"

log "unpacking pbs into ${STAGING}"
tar xzf "${TMP_TGZ}" -C "${STAGING}" --strip-components=1

# -----------------------------------------------------------------------------
# 2. pip install the wheel into the bundled Python
# -----------------------------------------------------------------------------

BUNDLED_PY="${STAGING}/bin/python3"
log "installing hololab into bundled Python"
"${BUNDLED_PY}" -m pip install --no-cache-dir --no-compile --upgrade pip
"${BUNDLED_PY}" -m pip install --no-cache-dir --no-compile "${REPO_ROOT}"

# -----------------------------------------------------------------------------
# 3. Rewrite console-script shebangs so the artifact is relocatable
# -----------------------------------------------------------------------------

log "rewriting shebangs in ${STAGING}/bin/"
for script in "${STAGING}/bin/hololab" "${STAGING}/bin/pip" "${STAGING}/bin/pip3"; do
  [ -f "${script}" ] || continue
  # Snip the first line (the bad shebang) and prepend the trampoline.
  ORIGINAL_BODY="$(tail -n +2 "${script}")"
  {
    cat "${TRAMPOLINE}"
    printf '%s\n' "${ORIGINAL_BODY}"
  } > "${script}.new"
  chmod +x "${script}.new"
  mv "${script}.new" "${script}"
done

# -----------------------------------------------------------------------------
# 4. Sanity check + tar
# -----------------------------------------------------------------------------

log "sanity: ${STAGING}/bin/hololab version"
"${STAGING}/bin/hololab" version

ARTIFACT="${DIST_DIR}/hololab-linux-x64.tar.gz"
log "tarring → ${ARTIFACT}"
tar czf "${ARTIFACT}" -C "${DIST_DIR}" hololab/

SIZE="$(du -h "${ARTIFACT}" | cut -f1)"
log "done: ${ARTIFACT} (${SIZE})"
