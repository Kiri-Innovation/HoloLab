# Weight cache client

```
hololab weights fetch --spec weights.json --only model --endpoint http://localhost:8080 --fallback error
hololab weights resolve --spec weights.json --only model --json
```

`--mirror` aliases `--endpoint`: both identify a registry base URL, including
any deployment prefix, before `/weights`. Endpoint precedence is CLI,
`HOLOLAB_WEIGHTS_ENDPOINT`, then no registry (HF). The default fallback is HF.
No endpoint always selects HF, including with `--fallback error`.

The spec follows the existing `items` format: `id`,
`hf:owner/repo@revision`, optional `patterns`, `requires`, `sha256`
and informational `bytes`. IDs select a transitive dependency closure.
Registry names use the source's `owner/repo`, and revision uses its source
revision. Registry revisions preserve case and allow
nonempty tags, rejecting slashes, backslashes, `..`, `.` and control characters.
Without an endpoint, fetch still requires a 40-character HF commit SHA. Offline
resolve accepts safe tags without an endpoint.
The deployed resolve response supplies `weight.sha` (legacy top-level `digest`
or `dir_sha` is also accepted); manifests are fetched from
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

## Uploading blobs from a data machine

Create a UTF-8 JSON allowlist, for example `["config.json","model.safetensors"]`:

```bash
hololab weights publish --root /data/model --name community/model \
  --paths /data/model-paths.json --publisher-env ~/.hololab/publisher.env \
  --resume-dir /data/upload-state --part-size-mib 64 --dry-run
# Remove --dry-run to upload. Ledger registration is a separate server-side step.
```

Exactly one of `--paths` and `--manifest-file` is required. There is no automatic
recursive directory scan: only explicitly listed files are hashed and uploaded.
With `--manifest-file /data/original-manifest.json`, the manifest's paths are the
allowlist; unrelated files under the root are ignored. All listed files must
match their declared sizes and SHA256 hashes. Source roots, selected files and
intermediate source directories must not be symlinks; files must be regular.

Publishing imports `qcloud_cos` only when uploading. Install `cos-python-sdk-v5`
in the CLI environment for uploads. The publisher env file must provide
`TENCENT_SECRET_ID`, `TENCENT_SECRET_KEY`, `COS_BUCKET`, and `COS_REGION`.
Assignments are parsed as literals, never executed. `COS_PREFIX` is ignored.
Keep the credentials file and resume directory outside the source root.

Generated manifests use exactly `{"format":1,"files":[...]}`. Each entry has
exactly `path`, `bytes`, and `sha256`; paths are sorted by their UTF-8 bytes,
without Unicode normalization. JSON uses literal UTF-8 without BOM, compact
separators, and no trailing newline. The parser enforces the server's 16 MiB
limit, duplicate-key/path rejection, safe paths, lowercase SHA256, exact field
sets and integer size/total limits. Original manifests are parsed as UTF-8 with
optional BOM and uploaded byte-for-byte, including whitespace and BOM. Their
identity is the hash of stored bytes, never reserialized JSON.

COS objects use these fixed keys:

- `WEIGHTS/blobs/sha256/<file_sha256>`
- `WEIGHTS/manifests/sha256/<manifest_sha256>/manifest.json`

Names must match the server's lowercase
`[a-z0-9][a-z0-9_-]{0,62}/[a-z0-9][a-z0-9_-]{0,126}` rule. Uppercase names such
as `Wan-AI/Wan2.1-T2V-14B` are rejected, never automatically normalized or renamed.
Choose the intended lowercase ledger name explicitly. Publish has no revision
option and does not register revisions; registration on the server must use its
`[a-z0-9][a-z0-9._-]{0,63}` rule (`v1` is valid). Existing fetch/resolve revision
handling is unchanged.

The name is reported for subsequent server-side registration; the CLI does not
write the ledger. The manifest is uploaded only after all blobs complete.
Dry-run prints JSON with the file list, keys, manifest digest, and total bytes;
it needs neither credentials nor the COS SDK and writes no resume state.

The default resume directory is `~/.hololab/weights-upload`. JSON checkpoints
record bucket, region, object key, size, part size, upload ID and acknowledged
part ETags, and are atomically replaced after each part. Retry with the same
resume directory and part size to skip acknowledged parts and completed blobs.
A local lock serializes invocations sharing the directory. Empty files use a
single empty PUT; other files use multipart uploads. Source files must remain
unchanged during publishing. Keep checkpoints until the upload completes;
abandoned multipart uploads should be expired with a COS lifecycle policy.
