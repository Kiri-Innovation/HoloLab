# Registry URL contract verification — 2026-10-26

Scope: this repository only. No service restart, deployment, push, remote publication,
yank/unyank, or submodule modification. Release field names and installation hash
checks remain unchanged. Only HTTP package resolution changed; local legacy
publication directories retain a read-only index.json fallback.

## Results

1. **Online installation: blocked, not claimed as passing.** Public catalog inspection
   found three packages and five releases, all yanked. The hello package has a valid
   code identity; the other two transport-test packages declare all-zero code hashes.
   `resolve("registry-smoke/hello-20261025184133@0.0.1", [root], [])` succeeded.
   `install(...)` correctly stopped with `RegistryError: 版本已 yank, 拒绝新安装`.
   A usable online release or publication credentials are required for this acceptance
   item. Local real HTTP installation with the new paths passed (test below).
2. **Online archive hash: passed.** GET returned 200 and 343 bytes from
   `https://packs.xenotech.studio/files/2a0337cf8bc140d6c639ef686f6209c3e3245481a4cedbbc29c7ff05d60fa891.tgz`.
   SHA-256 of downloaded bytes, Release.archive_sha256, and the filename all equal
   `2a0337cf8bc140d6c639ef686f6209c3e3245481a4cedbbc29c7ff05d60fa891`.
   The separate code_sha256 is
   `d9507411fd839fd3b4822711ac1f5dfc4526ffbb72ebf0f2764f6249ae525688`.
3. **Code identity enforcement: passed locally.** The HTTP test changes stg_state.py,
   repacks it, and updates archive_sha256, archive_bytes, unpacked_bytes, and the
   content-addressed archive URL. Installation passes the archive gate but raises
   HashMismatch at the code comparison; no final installation is promoted.
   A separate corrupt-archive test verifies extraction is never reached.
4. **Per-package requests: passed.** Instrumented online resolve and install made
   exactly two metadata requests, both to
   `https://packs.xenotech.studio/packs/registry-smoke/hello-20261025184133/`.
   No catalog or index.json was requested by either operation. Independent catalog
   probes were used only to locate an installable package. Local HTTP install
   requests are exactly `/mirror/packs/kiri/demo/` (resolve), the same path (install),
   then `/mirror/files/<archive_sha256>.tgz`. 404/503 never trigger a global index read.
5. **Legacy metadata redirects: passed online.** With redirect following disabled:
   `/simple/` -> 302, Location `/packs/`;
   `/simple/registry-smoke/hello-20261025184133/` -> 302, Location
   `/packs/registry-smoke/hello-20261025184133/`. Following each yields 200 and JSON.
6. **Bilingual docs/build: passed.** Endpoint tables match in both languages.
   All five Release archive examples in each document use files/<archive_sha256>.tgz,
   with the filename matching their archive_sha256 and differing from code_sha256.
   Old route strings remain only in the explicit compatibility/retirement note.
   `cd website && npm run build` exits 0: TypeScript, Vite, and localized HTML for
   52 routes. Vite reports a non-fatal bundle-size warning.

## Deployment discrepancy

The exact old archive URL
`https://packs.xenotech.studio/packages/registry-smoke/hello-20261025184133/0.0.1/d9507411fd839fd3b4822711ac1f5dfc4526ffbb72ebf0f2764f6249ae525688.tgz`
returns **302**, Location
`/files/2a0337cf8bc140d6c639ef686f6209c3e3245481a4cedbbc29c7ff05d60fa891.tgz`,
not the stated retirement-contract 404. Following returns 200/343 bytes, including
with a unique query and Cache-Control: no-cache. Documents distinguish the target
retirement contract from this observation; the new client does not use this route.

## Reproducible local regression checks

```sh
python -m pytest tests/test_pack_registry.py tests/test_pack_registry_urls.py tests/test_content_scope.py tests/test_package_identity.py -q
# 51 passed; 6 existing dependency/lifecycle deprecation warnings
python -m ruff check hololab/pack_registry.py hololab/node/config.py tests/test_pack_registry.py tests/test_pack_registry_urls.py
# All checks passed
cd website && npm run build
```

The new HTTP tests are in tests/test_pack_registry_urls.py. Online probe logs and
sample archive are retained locally under /tmp/hololab-registry-url-evidence/;
they contain no credentials. Live publication and a successful live installation
remain unverified; the local HTTP success does not substitute for that requirement.
