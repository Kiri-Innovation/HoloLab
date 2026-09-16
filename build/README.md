# HoloLab build scripts

Produces self-contained `hololab-<platform>.tar.gz` artifacts using
[python-build-standalone](https://github.com/astral-sh/python-build-standalone)
as the base interpreter. End users need no Python installed — they extract
the tarball and run `./hololab/bin/hololab`.

See [`docs/architecture.md#distribution`](../docs/architecture.md#distribution)
for the design rationale (light deps only, torch stays in user conda envs).

## Layout

```
build/
├── build-linux.sh        # Build linux-x64 artifact
├── sh-trampoline.tmpl    # sh-based shebang trampoline for relocatable shims
└── README.md             # this file
```

Future platforms (`build-darwin-arm64.sh`, `build-darwin-x64.sh`,
`build-windows.ps1`) share the same structure. Cross-compilation is not
supported — each artifact must be built on its target OS.

## Running locally

```bash
cd hololab            # repo root
make build-frontend   # produce hololab/frontend/dist/ if not fresh
./build/build-linux.sh
# → dist/hololab-linux-x64.tar.gz
```

Requires: `bash`, `tar`, `curl`, `python3` on PATH (only to bootstrap the
first pip install into the pbs venv). The produced artifact does not depend
on any of these.

## Acceptance check

```bash
tar xzf dist/hololab-linux-x64.tar.gz -C /tmp/
/tmp/hololab/bin/hololab --version
/tmp/hololab/bin/hololab start   # sanity smoke
```

`--version` must print `hololab 0.0.1` (or current). If it fails with
`bad interpreter`, the shebang rewrite step in `build-linux.sh` did not run
correctly.
