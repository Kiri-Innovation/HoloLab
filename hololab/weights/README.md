# Weight cache client

```
hololab weights fetch --spec weights.json --only model --endpoint http://localhost:8080 --fallback error
hololab weights resolve --spec weights.json --only model --json
```

`--mirror` aliases `--endpoint`: both identify a registry base URL, including
any deployment prefix, before `/weights`. Endpoint precedence is CLI,
`HOLOLAB_WEIGHTS_ENDPOINT`, then no registry (HF). The default fallback is HF.
No endpoint always selects HF, including with `--fallback error`.

The spec follows the existing `items` format: `id`, immutable
`hf:owner/repo@40-character-commit`, optional `patterns`, `requires`, `sha256`
and informational `bytes`. IDs select a transitive dependency closure.
Registry names use the source's `owner/repo`, and revision uses its commit.
Resolve responses supply `digest` (or `dir_sha`); manifests are fetched from
`/weights/manifests/<digest>.json` and hashed as raw bytes, without Unicode
normalization. Every manifest file requires `path`, `sha256`, and `bytes`.
The client uses resolve rather than downloading the full registry index.

Cache precedence: `--cache-dir`, `HOLOLAB_WEIGHTS_HOME`, then node config.
Node config uses `cache_dirs.weights`, otherwise `cache_root/hololab`, otherwise
`<HoloLab data directory>/node/weights/hololab`. Setup hooks and jobs receive
the same injected variable. Standalone Python functions read the same variable.
`resolve_weights` is offline and raises on missing files or digest mismatch.
Existing files without a receipt can be resolved when the spec supplies their
SHA256 hashes; otherwise a fetch establishes verified local receipts.

Files are published atomically after validation, under per-file POSIX locks.
Registry partial transfers resume through redirects using Range and validate
Content-Range. A server returning 200 restarts the partial; an invalid 416
clears it and reports failure (or falls back to HF), allowing a fresh retry.
Receipts retain local SHA256 values; a partial selection cannot certify a full
repository. HF downloads validate the HF etag, then hardlink (or copy across
filesystems) into the managed cache. This requires an already installed
`huggingface_hub`; the client never installs dependencies.

Default stdout is `ID<TAB>absolute snapshot directory`; `--json` returns an
ID-to-directory object. Diagnostics go to stderr. Exit codes: 0 success,
2 invalid arguments/spec, 1 unavailable or invalid weights.
