---
title: Algorithm package registry API reference
group: Collaboration and reproducibility
order: 80
---
# Algorithm package registry API reference

**The registry service is not open source; this document is its API reference.** It describes the contract for third-party publishing, discovery, download, and installation clients without requiring access to the service source. Documentation verified: 2026-10-26. Examples come from actual HTTP responses or recorded acceptance checks. Timestamps, package counts, and yank states describe those samples, not the current catalog or its size.

Use `https://packs.xenotech.studio/` as the source root in local configuration, not `/simple/`. A recipe records `scope/name@version` and the expected code hash; the source address is separate from package identity.

## 1. Endpoints and shared conventions

| Method | Path | Purpose | Authentication |
|---|---|---|---|
| GET / HEAD | `/simple/` | List package names | None |
| GET / HEAD | `/simple/<scope>/<name>/` | All committed versions of one package | None |
| GET / HEAD | `/packages/<scope>/<name>/<version>/<sha>.tgz` | Download an archive; sha is code_sha256 | None |
| GET / HEAD | `/index.json` | Dynamically generated, read-only compatibility index | None |
| GET | `/healthz` | Restricted diagnostics; **public requests return 404** | No application token check, but not exposed publicly |
| POST | `/publish` | Publish an immutable version | Bearer token |
| POST | `/yank` | Discourage a version and prohibit new installations | Bearer token |
| POST | `/unyank` | Permit new installations again | Bearer token |

Public reads require no token. All three write endpoints require `Authorization: Bearer <token>`. There are no APIs for registration, login, token issuance, deletion, overwriting versions, updating evidence, bulk publication, or search. The service provider supplies tokens separately to trusted publishers.

- Send requests directly over HTTPS. HTTP redirects to the same HTTPS path with **308**; never send a token over plaintext first.
- Use the paths exactly as listed: package read paths end in `/`, write endpoints do not. Do not depend on slash correction or redirects.
- JSON uses UTF-8. Object key order is not significant. Send the documented field types without relying on coercion; NaN, Infinity, and other nonstandard JSON numbers are forbidden.
- `/simple/`, package metadata, and `/index.json` have no pagination, cursors, filters, version query parameters, or conditional-request contract. Undefined query parameters do not filter results. All versions of a package arrive together, with no promised dictionary order.
- The three metadata endpoints accept `Accept: application/vnd.pypi.simple.v1+json` and return that Content-Type. Omitting Accept returns `application/json`. Selection currently tests whether the Accept text contains that media type; **full q-value negotiation is not implemented**. `Accept: text/html` still returns JSON, not HTML or 406.
- The URL structure borrows from the PyPI simple index, but the body is the **HoloLab Index/Release** schema below, not PyPI's files/hashes schema. It is not a pip installation endpoint.
- SQLite is the metadata source of truth. The old object-storage JSON index is no longer read or written. Publications become visible only after commit. Listing names and reading versions are separate requests, not a shared snapshot.
- There is no public browser CORS contract. A recorded OPTIONS request to `/simple/` returned 403. Cross-origin browser writes face preflight restrictions; integrate from an application server or command-line client.

## 2. Package identity, versions, and two hashes

### 2.1 Names and versions

A canonical name is `scope/name`. Both components are lowercase ASCII identifiers:

| Field | Pattern and constraints |
|---|---|
| scope | `^[a-z0-9][a-z0-9_-]*$`, 1–63 characters |
| name | `^[a-z0-9][a-z0-9_-]*$`, 1–127 characters; write APIs receive only the name component, without scope/ |
| version | `^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$`; at most 64 characters in write requests |
| Exact reference | `scope/name@version`; no ranges or automatic latest-version selection |

`kiri/foo@1.2.3` is valid. Uppercase letters, dotted names, whitespace, Chinese characters, leading `_` or `-`, extra `/`, `..`, and URL-encoded path bypasses are not valid canonical names. Versions such as `v1.2.3`, `1.2`, `01.2.3`, `1.2.3-rc1`, `1.2.3+build`, and ranges are rejected.

**Aliases are resolved by the client: `foo ≡ kiri/foo`.** Add the default scope before constructing a URL: `foo@1.2.3` uses `/simple/kiri/foo/`, not `/simple/foo/` (recorded response: 404). Write APIs always receive separate `scope="kiri"` and `name="foo"` fields. A manifest name may use the same unscoped alias, but its canonical identity must match the upload metadata. There is no PyPI-style case folding or normalization that merges `.`, `_`, and `-`.

HoloLab supports `1.2.3+` as a runnable development version. **Publication, yank, and unyank reject the trailing `+`.** Freeze the package to a three-component numeric release first. The archive's manifest.version must exactly match metadata.version.

### 2.2 Keep archive integrity separate from code identity

| Field | Hashed input | Server responsibility | Client responsibility |
|---|---|---|---|
| archive_sha256 | Raw compressed `.tgz` bytes, including gzip/tar metadata | Recompute while streaming; compare the declaration and read back newly written or existing objects | Recompute on download and verify archive_bytes |
| code_sha256 | Code identity within HoloLab's declared scope | Validate 64 lowercase hexadecimal characters and record the publisher's value; **do not recompute it** | Compare with the recipe's expected hash, then recompute from extracted files |

Both values are 64-character lowercase hex strings without a `sha256:` prefix. Repacking unchanged code can change archive_sha256. The archive URL's `<sha>` is **code_sha256**, not archive_sha256; prefer resolving Release.archive over constructing a path yourself.

`evidence` and `external` are publisher declarations, not server signatures or proofs of integrity. Comparing evidence.sha256 with code_sha256 is not independent verification. Section 12 defines FORMAT=2 code identity.

A published name and version remain reserved permanently, including after yank. Identical contents, a new token, or a repacked archive do not authorize replacement. Publish a new version, or retry the original operation with its original idempotency key.

Object storage currently permits public writes, so a third party can bypass the publication API and alter archive objects. Immutability is an API guarantee, not absolute storage-level tamper protection. Download clients must verify both hashes; recipes should pin a trusted expected code_sha256.

## 3. GET /simple/: list package names

Use this endpoint to display the catalog. Installation of a known package can go directly to its package URL.

**Request:** no body, authentication, or parameters.

```sh
curl --fail-with-body -H 'Accept: application/vnd.pypi.simple.v1+json' \
  'https://packs.xenotech.studio/simple/'
```

**Response:** 200 with a top-level projects array. Each entry has only name, a canonical `scope/name` string. Names are sorted and deduplicated. Any package with a committed version appears, **even if all its versions are yanked**. An empty catalog returns an empty array, without total, next, or cursor.

Recorded response body, 115 bytes before whitespace formatting; sample entries do not define the current catalog:

```json
{
  "projects": [
    {
      "name": "registry-smoke/hello-20261025184133"
    },
    {
      "name": "registry-smoke/simple-check-20261026114725"
    }
  ]
}
```

Headers include `Cache-Control: no-store` and `Vary: Accept`. HEAD has no body and returns the same status and relevant representation headers as GET.

**Errors:** methods other than GET/HEAD usually receive public-edge 403 HTML. Service failures can produce 502/504; do not interpret them as an empty catalog. There is no pagination, so apply a reasonable response-size limit.

## 4. GET /simple/<scope>/<name>/: read package versions

Retrieve every committed version of a package, including yanked versions, for exact-version lookup, display, or installation checks.

**Request:** no body or authentication. Path parameters follow section 2.

```sh
curl --fail-with-body -H 'Accept: application/vnd.pypi.simple.v1+json' \
  'https://packs.xenotech.studio/simple/registry-smoke/hello-20261025184133/'
```

**Response:** 200 with an Index object:

| Field | Type | Meaning |
|---|---|---|
| format | integer | Currently 1; distinct from evidence.format and the PyPI protocol version |
| authority | string | Currently hololab-central; configured mirrors should share the expected authority, without silently mixing registries |
| releases | object<string, Release> | Each key equals `Release.name + "@" + Release.version`; contains only the requested package |

### Complete Release fields

Current responses include every field below. Nullable fields use JSON null, never the string "null".

| Field | Type | Meaning or source |
|---|---|---|
| name | string | Canonical scope/name, not the name component alone |
| version | string | Published three-component numeric version, without development + |
| code_sha256 | string | Publisher-declared code identity, 64 lowercase hex characters |
| archive_sha256 | string | SHA-256 of uploaded archive bytes, verified by the server |
| archive | string | Safe path relative to the **source root URL**, such as packages/scope/name/version/code_sha256.tgz; not relative to the package metadata URL |
| archive_bytes | integer > 0 | Exact compressed archive size |
| unpacked_bytes | integer ≥ 0 | Server-validated sum of declared tar member sizes; valid directories contribute zero; excludes tar headers, padding, and filesystem block overhead |
| manifest | string | Relative manifest path inside the archive root; default manifest.yaml |
| env | string | manifest.runtime.env, or an empty string if absent; recording it does not mean the environment exists or will be installed |
| requires | object or null | Original manifest.requires mapping, or null; the registry defines no internal dependency schema and does not resolve or install dependencies |
| external | object | Original metadata.external declaration, default `{}`; no required internal fields or dependency-closure guarantee |
| evidence | object | Original metadata.evidence declaration, default `{}`; may contain format, hot/cold, or manifest, but neither their presence nor truth is verified |
| published_at | string | Release creation time in UTC ISO 8601 with timezone; examples use +00:00; unchanged by yank |
| publisher | string | Service-assigned label, currently trusted-publisher; not caller-settable or a digital signature |
| yanked | boolean | true prohibits new installations; metadata and archive remain readable |
| yank_reason | string or null | Current reason; null after unyank |
| yank_updated_at | string or null | Last effective state or reason change, UTC ISO 8601; initially null, retained after unyank |

Recorded response from a protocol test package, checked by a read request:

```json
{
  "format": 1,
  "authority": "hololab-central",
  "releases": {
    "registry-smoke/hello-20261025184133@0.0.1": {
      "name": "registry-smoke/hello-20261025184133",
      "version": "0.0.1",
      "code_sha256": "d9507411fd839fd3b4822711ac1f5dfc4526ffbb72ebf0f2764f6249ae525688",
      "archive_sha256": "2a0337cf8bc140d6c639ef686f6209c3e3245481a4cedbbc29c7ff05d60fa891",
      "archive": "packages/registry-smoke/hello-20261025184133/0.0.1/d9507411fd839fd3b4822711ac1f5dfc4526ffbb72ebf0f2764f6249ae525688.tgz",
      "archive_bytes": 343,
      "unpacked_bytes": 261,
      "manifest": "manifest.yaml",
      "env": "test",
      "requires": null,
      "external": {
        "dependency_discovery_complete": false,
        "uncovered": [],
        "undeclared": []
      },
      "evidence": {
        "cold": {
          "baseline_at": null,
          "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
          "stats": {
            "bytes": 0,
            "files": 0,
            "links": 0,
            "top_level_names": []
          },
          "verification": "not_checked"
        },
        "controls": {
          "manifest_sha256": "b3975205157d30ca8eb02325f435f7b6684b2419b726cecce74bc2dec9cc93f5",
          "packignore_sha256": null
        },
        "dependency_discovery_complete": false,
        "fixed_excluded": {
          "bytes": 0,
          "files": 0,
          "links": 0,
          "top_level_names": []
        },
        "format": 2,
        "git": {
          "commit": null,
          "dirty": null,
          "tree_sha": null
        },
        "hot": {
          "sha256": "579c7a744ab562386960c543b49b2d8889cef529ff539c6589fc21fc4f813f5c",
          "stats": {
            "bytes": 32,
            "files": 1,
            "links": 0,
            "top_level_names": [
              "hello.py"
            ]
          },
          "verification": "verified_at_submission"
        },
        "ignored": {
          "bytes": 0,
          "files": 0,
          "links": 0,
          "top_level_names": []
        },
        "manifest": {
          "excluded_fields": [
            "published_as",
            "published_at",
            "version",
            "yank",
            "yank_reason",
            "yanked",
            "yanked_at"
          ],
          "normalized": {
            "apiVersion": "hololab.dev/v1",
            "exec": {
              "shell": "python \"{{ package_root }}/hello.py\""
            },
            "inputs": {},
            "kind": "Algorithm",
            "name": "registry-smoke/hello-20261025184133",
            "outputs": {
              "out": {
                "tags": [
                  "text"
                ]
              }
            },
            "root": ".",
            "runtime": {
              "env": "test"
            }
          },
          "raw_sha256": "b3975205157d30ca8eb02325f435f7b6684b2419b726cecce74bc2dec9cc93f5",
          "sha256": "263c8fc3d3f9746b0999dbae2eefcf8b9a0875244a53aff9693fe6f3ee81feaf"
        },
        "quality_hints": [],
        "recorded_at": 1792924894.8461387,
        "root": ".",
        "sha256": "d9507411fd839fd3b4822711ac1f5dfc4526ffbb72ebf0f2764f6249ae525688",
        "source": "registry-smoke/hello-20261025184133@0.0.1",
        "uncovered": [],
        "undeclared": []
      },
      "published_at": "2026-10-25T10:41:35.028765+00:00",
      "publisher": "trusted-publisher",
      "yanked": true,
      "yank_reason": "deployment smoke test pack; kept in archive as pipeline evidence, not recommended for use",
      "yank_updated_at": "2026-10-26T03:19:49.615178+00:00"
    }
  }
}
```

This sample release was yanked. It illustrates the protocol and is not an installation recommendation. Historical verification strings inside evidence are data, not proof that the current service recomputed code identity.

**Errors:**

- 404, recorded body `{"detail":"Package not found"}`: valid path, no committed versions.
- 422, recorded body `{"detail":"Invalid package name"}`: for example, a scope containing `!`.
- 404, recorded body `{"detail":"Not Found"}`: incorrect route shape, such as `/simple/foo/`.
- An existing package still returns 200 when the desired version is absent. **The client detects a missing exact-version key in releases**; there is no server-side version-query 404.

## 5. GET /packages/<scope>/<name>/<version>/<sha>.tgz: download an archive

Download the tar+gzip bytes identified by Release.archive. No authentication or body is needed; HEAD is also supported.

```sh
curl --fail-with-body --output package.tgz \
  'https://packs.xenotech.studio/packages/registry-smoke/hello-20261025184133/0.0.1/d9507411fd839fd3b4822711ac1f5dfc4526ffbb72ebf0f2764f6249ae525688.tgz'
```

**Response:** 200 with binary `.tgz` data, not JSON. The service proxies Tencent Cloud COS; **normal downloads do not return 302**. Clients need neither COS credentials nor request signing. Recorded representation headers:

```http
HTTP/2 200
Content-Type: application/gzip
Content-Length: 343
Cache-Control: no-store
Content-Disposition: attachment
```

Content-Disposition has no filename parameter; choose a local filename yourself. The recorded archive SHA-256 was `2a0337cf8bc140d6c639ef686f6209c3e3245481a4cedbbc29c7ff05d60fa891`, matching its Release. Object-storage headers such as ETag, Last-Modified, and Accept-Ranges may appear. **ETag is neither of the SHA-256 values in this contract.** Range and conditional downloads were not fully validated. If resuming a download, verify the complete final byte stream.

**Errors:** a path outside the public download rule may return 404 HTML. A matching path with a missing object may return COS 404 XML. Storage permission errors can return 403, and proxy failures 502/504. Handle status and Content-Type rather than assuming JSON; never install an error page.

Downloads do not check yank state. Yanked archives remain downloadable, so clients must enforce the new-installation prohibition using current metadata. A guessed downloadable URL also does not establish that a version is committed; obtain its Release first.

## 6. GET /index.json: read the compatibility index

This endpoint retains a site-wide Index for older clients. **It is generated dynamically from the database, not maintained as an authoritative file or written to the old COS index.json.** New clients should use package-level metadata.

```sh
curl --fail-with-body 'https://packs.xenotech.studio/index.json'
```

**Response:** 200 with the section 4 Index schema, containing all committed releases across the registry. No pagination or authentication. Catalog size changes as releases are committed. This endpoint is not a 302 redirect; no removal date has been announced.

```http
Content-Type: application/json
Cache-Control: no-store
Vary: Accept
Deprecation: true
Link: </simple/>; rel="successor-version"
```

**Recorded JSON example:** this complete response was captured on 2026-10-26. Its entries illustrate the schema, not the current catalog size. Requesting the vnd JSON media type changes only Content-Type, not the schema.

```json
{
  "format": 1,
  "authority": "hololab-central",
  "releases": {
    "registry-smoke/hello-20261025184133@0.0.1": {
      "name": "registry-smoke/hello-20261025184133",
      "version": "0.0.1",
      "code_sha256": "d9507411fd839fd3b4822711ac1f5dfc4526ffbb72ebf0f2764f6249ae525688",
      "archive_sha256": "2a0337cf8bc140d6c639ef686f6209c3e3245481a4cedbbc29c7ff05d60fa891",
      "archive": "packages/registry-smoke/hello-20261025184133/0.0.1/d9507411fd839fd3b4822711ac1f5dfc4526ffbb72ebf0f2764f6249ae525688.tgz",
      "archive_bytes": 343,
      "unpacked_bytes": 261,
      "manifest": "manifest.yaml",
      "env": "test",
      "requires": null,
      "external": {
        "dependency_discovery_complete": false,
        "uncovered": [],
        "undeclared": []
      },
      "evidence": {
        "cold": {
          "baseline_at": null,
          "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
          "stats": {
            "bytes": 0,
            "files": 0,
            "links": 0,
            "top_level_names": []
          },
          "verification": "not_checked"
        },
        "controls": {
          "manifest_sha256": "b3975205157d30ca8eb02325f435f7b6684b2419b726cecce74bc2dec9cc93f5",
          "packignore_sha256": null
        },
        "dependency_discovery_complete": false,
        "fixed_excluded": {
          "bytes": 0,
          "files": 0,
          "links": 0,
          "top_level_names": []
        },
        "format": 2,
        "git": {
          "commit": null,
          "dirty": null,
          "tree_sha": null
        },
        "hot": {
          "sha256": "579c7a744ab562386960c543b49b2d8889cef529ff539c6589fc21fc4f813f5c",
          "stats": {
            "bytes": 32,
            "files": 1,
            "links": 0,
            "top_level_names": [
              "hello.py"
            ]
          },
          "verification": "verified_at_submission"
        },
        "ignored": {
          "bytes": 0,
          "files": 0,
          "links": 0,
          "top_level_names": []
        },
        "manifest": {
          "excluded_fields": [
            "published_as",
            "published_at",
            "version",
            "yank",
            "yank_reason",
            "yanked",
            "yanked_at"
          ],
          "normalized": {
            "apiVersion": "hololab.dev/v1",
            "exec": {
              "shell": "python \"{{ package_root }}/hello.py\""
            },
            "inputs": {},
            "kind": "Algorithm",
            "name": "registry-smoke/hello-20261025184133",
            "outputs": {
              "out": {
                "tags": [
                  "text"
                ]
              }
            },
            "root": ".",
            "runtime": {
              "env": "test"
            }
          },
          "raw_sha256": "b3975205157d30ca8eb02325f435f7b6684b2419b726cecce74bc2dec9cc93f5",
          "sha256": "263c8fc3d3f9746b0999dbae2eefcf8b9a0875244a53aff9693fe6f3ee81feaf"
        },
        "quality_hints": [],
        "recorded_at": 1792924894.8461387,
        "root": ".",
        "sha256": "d9507411fd839fd3b4822711ac1f5dfc4526ffbb72ebf0f2764f6249ae525688",
        "source": "registry-smoke/hello-20261025184133@0.0.1",
        "uncovered": [],
        "undeclared": []
      },
      "published_at": "2026-10-25T10:41:35.028765+00:00",
      "publisher": "trusted-publisher",
      "yanked": true,
      "yank_reason": "deployment smoke test pack; kept in archive as pipeline evidence, not recommended for use",
      "yank_updated_at": "2026-10-26T03:19:49.615178+00:00"
    }
  }
}
```

**Errors:** unsupported methods return 403 HTML; service failures may return 502/504. Do not fall back to the old JSON directly on COS: it may remain permanently stale.

## 7. GET /healthz: restricted diagnostics

This diagnostic reports unfinished operations and committed releases. **It is not a publicly callable endpoint**, and there is no separate health-check token for external clients.

**Public request:** `GET https://packs.xenotech.studio/healthz` returned **404 text/html**, not JSON. That response does not mean the registry is unhealthy. Use `GET /simple/` for third-party connectivity checks.

A restricted, read-only diagnostic check recorded this 200 body:

```json
{"ready":true,"pending":0,"releases":3}
```

| Field | Type | Meaning |
|---|---|---|
| ready | boolean | true when pending is zero |
| pending | integer ≥ 0 | Publication or other operations not yet committed |
| releases | integer ≥ 0 | Committed releases, including yanked ones |

**Not ready:** the implementation returns the same schema with ready=false and HTTP 503 when pending is nonzero. That state was not deliberately induced. ready=true reflects this test only, not verification that every archive is downloadable. No external diagnostic address is provided.

## 8. POST /publish: publish an archive and its declarations

Create a new release version. The service validates the archive, uploads and verifies its object, then makes the committed version visible through metadata endpoints.

### 8.1 Request

- Send `Authorization: Bearer <token>` with the shown capitalization and spacing, not in the query string or JSON.
- Supply `Idempotency-Key: <operation-id>` for reliable recovery: 1–128 characters matching `^[A-Za-z0-9_.-]{1,128}$`. It is optional, but omitting it prevents reliable recovery if the initial response is lost.
- Use **multipart/form-data** with exactly one **metadata** text field and one **archive** file field. Neither a JSON body nor a raw gzip body is accepted.
- The metadata text is a JSON object, limited to **16,384 characters**. The archive is tar+gzip. Let the HTTP library generate the multipart boundary; do not manually send Content-Type without it.

| metadata field | Type | Required/default | Constraints and meaning |
|---|---|---|---|
| scope | string | Required | Section 2 rules |
| name | string | Required | Without scope/ |
| version | string | Required | Three-component release version, without + |
| code_sha256 | string | Required | Publisher-computed code identity, 64 lowercase hex characters |
| archive_sha256 | string | Required | SHA-256 of this upload's bytes, 64 lowercase hex characters |
| manifest | string | Default manifest.yaml | 1–1024 characters; safe relative archive path that must exist |
| evidence | object | Default `{}` | Publisher evidence, recorded unchanged and untrusted |
| external | object | Default `{}` | External-dependency declarations, recorded unchanged and untrusted |

**Unknown metadata fields are rejected.** Do not submit response fields such as archive, archive_bytes, unpacked_bytes, env, requires, published_at, publisher, or yanked. The service derives them or reads them from the manifest.

Request template using your own local files and a placeholder token:

```sh
curl --fail-with-body -X POST 'https://packs.xenotech.studio/publish' \
  -H 'Authorization: Bearer <token>' \
  -H 'Idempotency-Key: <operation-id>' \
  --form-string "metadata=$(cat metadata.json)" \
  -F 'archive=@package.tgz;type=application/gzip'
```

Replace `<operation-id>` with a valid value without angle brackets. `--form-string` prevents curl from treating `;` or `@` inside metadata as form-file syntax. The filename package.tgz is a transport label, not the package identity.

The following metadata comes from a recorded publication. **It deliberately declares an all-zero code hash to test transport. It is not an installable release. Compute your actual code_sha256; do not copy the zero value or reuse this existing version.**

```json
{
  "scope": "registry-smoke",
  "name": "simple-check-20261026114725",
  "version": "0.0.1",
  "manifest": "manifest.yaml",
  "code_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "archive_sha256": "178a676182714991b8afad3fc0e4f6878e251db146d402aebd8e6ee6d28fe253",
  "evidence": {
    "purpose": "API transport test; deliberately declared zero code hash, not an installable release"
  }
}
```

### 8.2 Success response

A new publication returns **201**. The JSON envelope contains release, the complete section 4 Release, and idempotency_key, the string actually used. A completed same-key retry returns **200** with the same envelope. Recorded 201 response:

```json
{
  "release": {
    "archive": "packages/registry-smoke/simple-check-20261026114725/0.0.1/0000000000000000000000000000000000000000000000000000000000000000.tgz",
    "archive_bytes": 232,
    "archive_sha256": "178a676182714991b8afad3fc0e4f6878e251db146d402aebd8e6ee6d28fe253",
    "code_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "env": "test",
    "evidence": {
      "purpose": "API transport test; deliberately declared zero code hash, not an installable release"
    },
    "external": {},
    "manifest": "manifest.yaml",
    "name": "registry-smoke/simple-check-20261026114725",
    "published_at": "2026-10-26T03:47:25.959122+00:00",
    "publisher": "trusted-publisher",
    "requires": null,
    "unpacked_bytes": 108,
    "version": "0.0.1",
    "yank_reason": null,
    "yank_updated_at": null,
    "yanked": false
  },
  "idempotency_key": "simple-check-20261026114725-0.0.1"
}
```

### 8.3 Idempotency, immutability, and recovery

| Situation | Result |
|---|---|
| New operation for a version neither published nor reserved | 201 on success |
| Same key, operation, and normalized metadata; operation completed | 200 with that operation's original release snapshot |
| Same key with different metadata or operation kind | 409, Idempotency key reused with different request |
| New key for an existing name/version, even with identical contents | 409, Version already published or reserved; never overwrite |
| Different contents for the same name/version | Validation may fail first with 422; a valid archive reaching the version-conflict check gets 409; never overwritten |
| Same-key operation still uploading | 409, Publication in progress; retry same Idempotency-Key; wait and retry unchanged |
| Temporary storage failure or uncertain outcome | 503; retain the original file, metadata, and key for an identical retry |
| Existing target object differs in bytes or size | 409 without overwrite; service-provider intervention may be needed |

Keys use **one service-wide namespace across all write endpoints**, not a namespace per package or token. Never share a key between publish, yank, and unyank. If omitted, a random key is generated and returned on success. A lost first response also loses that key, so production clients should persist a key before sending a request.

Request validation precedes the idempotency lookup. Even a completed operation requires valid multipart, the same complete archive, and matching declarations on retry. An empty file cannot probe operation status. Metadata object order does not affect the fingerprint; omitted defaults and equivalent explicit defaults are normalized. Retaining the original request materials is still preferable to repacking.

Timeout or 503 does not prove that publication failed. Retry with the original key and inspect package metadata. A reserved version from a failed operation may be absent from reads while a new-key publication still returns 409. No external API deletes such reservations.

A publication replay returns **the original operation snapshot**. If the release was later yanked, replaying publish may still show yanked=false. Fetch package metadata again for current state.

Recorded duplicate-version error:

```json
{"detail":"Version already published or reserved; never overwrite"}
```

**Other errors:** 400 form parsing; 401 authentication; 403 token scope; 413 request/archive size; 422 metadata, hash, or archive safety; 429 rate, concurrency, or daily quota; 503 storage. Section 10 gives details.

## 9. POST /yank and POST /unyank

### 9.1 POST /yank

Change only the yank flag and reason of a committed release. Identity and archive remain intact. Clients reject new installations; existing installations are not remotely removed.

**Request:** Bearer token, an optional independent Idempotency-Key, and `Content-Type: application/json`. Only these body fields are allowed:

| Field | Type | Required/default |
|---|---|---|
| scope | string | Required; section 2 rules |
| name | string | Required; without scope/ |
| version | string | Required; three-component release version |
| reason | string or null | Optional, default null; at most 1024 characters |

Recorded acceptance-check body, provided as evidence rather than an instruction to modify this public test package:

```json
{"scope":"registry-smoke","name":"simple-check-20261026114725","version":"0.0.1","reason":"Transport-only API test; declared code identity is intentionally unverified"}
```

Request template:

```sh
curl --fail-with-body -X POST 'https://packs.xenotech.studio/yank' \
  -H 'Authorization: Bearer <token>' -H 'Idempotency-Key: <new-operation-id>' \
  -H 'Content-Type: application/json' --data-binary @yank.json
```

**Response:** 200, `{"release": Release, "idempotency_key": string}`. Recorded response:

```json
{
  "release": {
    "archive": "packages/registry-smoke/simple-check-20261026114725/0.0.1/0000000000000000000000000000000000000000000000000000000000000000.tgz",
    "archive_bytes": 232,
    "archive_sha256": "178a676182714991b8afad3fc0e4f6878e251db146d402aebd8e6ee6d28fe253",
    "code_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "env": "test",
    "evidence": {
      "purpose": "API transport test; deliberately declared zero code hash, not an installable release"
    },
    "external": {},
    "manifest": "manifest.yaml",
    "name": "registry-smoke/simple-check-20261026114725",
    "published_at": "2026-10-26T03:47:25.959122+00:00",
    "publisher": "trusted-publisher",
    "requires": null,
    "unpacked_bytes": 108,
    "version": "0.0.1",
    "yank_reason": "Transport-only API test; declared code identity is intentionally unverified",
    "yank_updated_at": "2026-10-26T03:47:27.641769+00:00",
    "yanked": true
  },
  "idempotency_key": "e8a1afa232f84f0d873469766464aac1"
}
```

**Errors:** 401 authentication, 403 scope, 404 Release not found (including uncommitted releases), 409 key conflict, 413 body above 16 KiB, 422 invalid fields/key, or 429 limits. Unknown fields and invalid field types produce Invalid yank metadata.

### 9.2 POST /unyank

Permit new installations again by setting yanked=false and yank_reason=null. Archive and release identity do not change.

**Request:** the same schema, authentication, and headers as yank, sent to `/unyank`. reason may be omitted. A supplied reason is not retained in the result, but still contributes to the idempotency fingerprint and must remain unchanged on retry.

```sh
curl --fail-with-body -X POST 'https://packs.xenotech.studio/unyank' \
  -H 'Authorization: Bearer <token>' -H 'Idempotency-Key: <new-operation-id>' \
  -H 'Content-Type: application/json' --data-binary @unyank.json
```

The recorded unyank check used the same body as the yank check, with a separate operation. Recorded 200 response:

```json
{
  "release": {
    "archive": "packages/registry-smoke/simple-check-20261026114725/0.0.1/0000000000000000000000000000000000000000000000000000000000000000.tgz",
    "archive_bytes": 232,
    "archive_sha256": "178a676182714991b8afad3fc0e4f6878e251db146d402aebd8e6ee6d28fe253",
    "code_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "env": "test",
    "evidence": {
      "purpose": "API transport test; deliberately declared zero code hash, not an installable release"
    },
    "external": {},
    "manifest": "manifest.yaml",
    "name": "registry-smoke/simple-check-20261026114725",
    "published_at": "2026-10-26T03:47:25.959122+00:00",
    "publisher": "trusted-publisher",
    "requires": null,
    "unpacked_bytes": 108,
    "version": "0.0.1",
    "yank_reason": null,
    "yank_updated_at": "2026-10-26T03:47:27.820040+00:00",
    "yanked": false
  },
  "idempotency_key": "7fc7ecfab14348648215b8dafdb5e078"
}
```

### 9.3 Idempotency of state changes

- The same key, operation, and request return the original result without changing current state again. Reusing a key for a different operation or request returns 409.
- Yanking an already-yanked version with a new key returns 200. An unchanged reason preserves yank_updated_at; a different reason updates both reason and time. Omitting reason means null and may clear an earlier reason.
- Unyanking an already-unyanked version with a new key returns 200 without updating yank_updated_at when state is unchanged.
- State transitions or effective reason changes update the timestamp. published_at, both hashes, archive, and release identity stay unchanged.
- Replaying an old yank after a later unyank returns the old yanked=true snapshot but **does not yank again**. Use a new key for a new state change.

## 10. Authentication, validation, limits, and errors

### 10.1 Authentication failures

All three write endpoints check the Bearer token before parsing the body. Missing or incorrect tokens return 401. Token-free requests to each endpoint recorded the same body:

```json
{"detail":"Invalid bearer token"}
```

There is currently no WWW-Authenticate challenge header. Send the token proactively. A token lacking permission for the requested scope receives 403 with detail Token not authorized for this scope. Authorization never bypasses version uniqueness.

### 10.2 What the server validates

1. Identity fields, hash string format, manifest path, absence of extra metadata fields, and token scope.
2. Streaming archive SHA-256. Empty content or a mismatched declaration returns 422, `Archive SHA-256 mismatch`. code_sha256 is not recomputed.
3. Tar+gzip structure. Paths reject absolute forms, `..`, backslashes, colons, NUL, `.git` segments, and empty/root paths, with a 1024-character limit. Duplicate normalized paths and casefold collisions are rejected.
4. Only regular files and directories. Symlinks, hard links, devices, FIFOs, other special types, and sparse members are rejected. Directories must have zero size; member sizes cannot be negative; truncated files are rejected.
5. Decompression limits below, including actual decompressed tar bytes rather than only declared member sizes.
6. The manifest must exist as a file of at most 1 MiB and parse safely into the expected YAML structure. Its root, resolved relative to the manifest directory, must stay within the extraction root. Its canonical name and exact version must match the upload. If present, runtime.env must be a string and requires an object or null.
7. Archive/manifest failures and validation timeouts return 422, `Unsafe archive or invalid manifest`, without exposing detailed untrusted paths.
8. Version uniqueness and full byte-hash/size checks for existing objects. Matching bytes permit recovery of the existing operation only; differing bytes return 409 without overwrite.

Manifest validation covers registry requirements, **not certification that the algorithm will run**. The service does not execute code, install dependencies, scan for malware, or establish the completeness of evidence, external, or runtime dependencies.

| Limit | Current value | Failure behavior |
|---|---|---|
| Entire HTTP body | ≤520 MiB including multipart overhead | 413, possibly proxy HTML or JSON |
| Published archive | >0 and ≤512 MiB | Oversize: 413; empty: 422 |
| Publication metadata text | ≤16,384 characters | 422 Invalid publication metadata |
| yank / unyank body | ≤16 KiB | 413 Request too large |
| Tar members | ≤50,000 | 422 Unsafe archive or invalid manifest |
| Sum of tar member sizes | ≤2 GiB | Same as above |
| Decompressed tar bytes, including headers/padding | ≤ `min(2 GiB + 50,000×2048, max(1 MiB, archive_bytes×100))` | Same as above; not a blanket 100:1 limit for small archives |
| Manifest file | ≤1 MiB | Same as above |
| Validation resources | Approximately 45 seconds CPU and 90 seconds wall time, with bounded memory | Failure to finish usually returns 422; retries must not be used to bypass validation |
| Active writes | At most 2 shared across the three write endpoints | 429 Too many active writers |
| Daily publication reservations | At most 100 per UTC day, service-wide | 429 Daily publication quota reached |
| Daily reserved archive bytes | At most 2 GiB per UTC day, service-wide | Same as above |

MiB means 1024² bytes; GiB means 1024³. Daily quotas count reserved publish operations, including failed operations that obtained a reservation. Successful idempotent replay does not count again. Yank does not consume a publication count.

**nginx rate limiting:** all three write endpoints share **60 requests/minute** per connection IP, with `burst=20` and `nodelay`. Requests within the burst allowance run immediately; excess requests receive **429**, usually HTML. This is rate shaping, not a counter resetting each calendar minute. Read endpoints have no dedicated limit_req in this configuration; that is not an unlimited-capacity guarantee. Request-body read inactivity times out after 60 seconds; write upstream-response read inactivity after up to 300 seconds. Other network or proxy timeouts may occur sooner. Retry-After is not promised; use exponential backoff with jitter.

### 10.3 Status and error reference

Application errors normally contain detail:string in a JSON object; storage-related 503 responses also contain retry:string. **“Implementation review” below means a possible error checked against the implementation, not an observed HTTP response.** Use Content-Type for non-JSON errors. Do not classify business outcomes solely by English detail text, which may expand over time.

| Status | Situation | Body or identifier | Evidence |
|---|---|---|---|
| 200 | Reads, yank/unyank, completed publication replay | Corresponding success schema | Recorded calls |
| 201 | New release published | release + idempotency_key | Recorded acceptance check |
| 308 | HTTP to HTTPS | Location points to HTTPS | Configuration review; no plaintext token sent |
| 400 | Invalid Content-Length, multipart parsing or field counts | Application detail may be Invalid content length; parser may supply its own detail | Implementation review |
| 401 | Missing/invalid Bearer token | `{"detail":"Invalid bearer token"}` | Observed at all three write endpoints |
| 403 | Token lacks scope access | Token not authorized for this scope | Implementation review |
| 403 | Disallowed method, e.g. GET /publish or OPTIONS /simple/ | text/html, 403 Forbidden | Observed |
| 404 | No package | `{"detail":"Package not found"}` | Observed |
| 404 | Wrong route shape | `{"detail":"Not Found"}` or proxy HTML | Observed |
| 404 | Missing/uncommitted yank or unyank target | Release not found | Implementation review |
| 404/403 | Missing archive or storage access denied | May be object-storage XML without detail | Implementation/proxy review; no induced storage fault |
| 405 | Method unsupported by application | Framework Method Not Allowed; public edge usually intercepts with 403 | Implementation review; do not depend on public 405 |
| 409 | Version published or reserved | `{"detail":"Version already published or reserved; never overwrite"}` | Recorded acceptance check |
| 409 | Key reused with different contents or operation | Idempotency key reused with different request | Implementation review |
| 409 | Same-key upload still running | Publication in progress; retry same Idempotency-Key | Implementation review |
| 409 | Existing object differs | Existing COS object differs; refusing overwrite / Existing COS object differs in size | Implementation review |
| 409 | Recovery archive mismatch | Pending archive differs from journal | Implementation review; provider intervention needed |
| 413 | Oversize request/file | Request too large / Archive exceeds 512 MiB, or proxy HTML | Implementation review; no oversize upload |
| 422 | Invalid identity/metadata | Invalid publication metadata / Invalid yank metadata / Invalid package name | Package path observed; others reviewed |
| 422 | Invalid idempotency key | Invalid Idempotency-Key | Implementation review |
| 422 | Invalid form shape | Expected exactly one metadata field and one archive file / archive must be a file | Implementation review; parsing may return 400 first |
| 422 | Archive hash mismatch/empty archive | Archive SHA-256 mismatch | Implementation review |
| 422 | Unsafe tar, invalid manifest, validation timeout | Unsafe archive or invalid manifest | Implementation review |
| 422 | Object path violates prefix protection | COS key must remain within PACKS/ | Implementation review; ordinary valid clients should not trigger this |
| 429 | Proxy rate limit | Usually HTML, 429 Too Many Requests | Configuration review; no production load test |
| 429 | Active-write or daily quota limit | Too many active writers / Daily publication quota reached | Implementation review |
| 503 | Temporary storage failure or missing recovery prerequisites | COS HEAD failed / COS PUT failed; outcome must be reconciled / COS object verification failed / Pending publication spool missing; operator recovery required, etc.; also retry | Implementation review; no injected production fault |
| 503 | Restricted health diagnostic not ready | ready/pending/releases schema, no detail | Implementation review; not public |
| 500 | Unclassified internal failure | JSON not guaranteed | General failure boundary; not induced |
| 408/502/504 | Timeout, proxy connection or upstream-response failure | Usually HTML; JSON not guaranteed | Configuration/network boundary; not induced |

Storage 503 responses use retry text `Retry the identical request with the same Idempotency-Key`. Errors containing operator recovery required may persist even with identical retries; contact the service provider. Interpret status together with current metadata and the operation record. Not every 409 is automatically retryable.

## 11. Caching, proxies, and credentials

Metadata cache headers below describe successful responses. Application errors such as 404/422 may not carry the same headers. Clients and CDNs should not cache errors as durable package state.

| Path | Current cache behavior | Header and proxy details |
|---|---|---|
| `/simple/`, package metadata, `/index.json` | Cache-Control: no-store, Vary: Accept; proxy caching disabled | Authorization/Cookie are not actively stripped, but reads need neither and clients should omit them; no promised ETag/304 negotiation |
| `/publish`, `/yank`, `/unyank` | Cache-Control: no-store; do not cache requests or results | Authorization is forwarded for validation; the service reconstructs Host and forwarding-origin headers; caller-supplied forwarding IP headers cannot bypass connection-IP rate limits |
| `/packages/...` | Site replaces object cache headers with no-store; proxy caching disabled | **Authorization and Cookie are cleared** before COS, and Host is rewritten; other headers such as Accept/Range may still be forwarded |

Do not force metadata caching at a CDN: stale data can hide a yank. Never cache write endpoints. Given current public-write object storage, do not force long-lived CDN caching of archive responses either. A client may keep a **local content cache verified with both hashes**, but every new installation still needs current metadata and a yank check. Hash mismatch is a hard stop, not a reason to try another cached copy or source until one succeeds.

Do not integrate directly with the old COS index or treat COS hostnames and storage layout as stable client APIs. Archive downloads currently have no 302 redirect. If a general HTTP client follows redirects in future, it must not forward Bearer tokens or cookies to another origin. Downloads require no token in the first place.

## 12. Minimal client and code-identity verification

### 12.1 Installation pseudocode

This flow installs a new package. Listing is optional UI work; exact-version installation does not require the full catalog. Errors terminate the installation without executing archive code.

```text
base = "https://packs.xenotech.studio/"

# Optional catalog display; unnecessary for a known package
projects = GET_JSON(base + "simple/").projects
show(projects[*].name)

(name, version) = parse_exact_spec(user_spec)
name = canonical_name(name)            # foo -> kiri/foo; then validate scope/name
reject_dev_plus_or_invalid_version(version)
index = GET_JSON(base + "simple/" + name + "/",
                 Accept="application/vnd.pypi.simple.v1+json")
require index.format == 1
require expected_authority == index.authority
require every release.name == name
r = index.releases[name + "@" + version]  # Missing key -> version not found; never select a nearby version
validate_release_schema_and_key_identity(r)
if expected_code_sha256 is set and r.code_sha256 != expected_code_sha256:
    HARD_STOP("Code identity differs from recipe; do not switch sources")
if r.yanked: STOP("New installation prohibited", r.yank_reason)

validate_safe_relative_path(r.archive)  # Reject absolute paths, .., backslashes, colons, and empty paths
url = resolve_relative(base, r.archive) # Relative to source root, not /simple/scope/name/
require same_origin(url, base)
bytes, computed_archive_sha = DOWNLOAD_AND_STREAM_SHA256(url,
                                  maximum_bytes=r.archive_bytes, no_token=true)
require len(bytes) == r.archive_bytes
if computed_archive_sha != r.archive_sha256: HARD_STOP("Archive integrity mismatch")

staging = new_temporary_install_directory()
SAFE_EXTRACT_TAR_GZIP(bytes, staging,
    reject_absolute_and_parent_paths=true,
    reject_links_special_sparse_and_duplicates=true,
    limit_unpacked_bytes=r.unpacked_bytes,
    independent_decompression_and_member_limits=true)
manifest = safe_path_inside(staging, r.manifest)
require canonical_name(manifest.name) == r.name
require manifest.version == r.version
require resolved_manifest_root_inside(staging)
computed_code_sha = recompute_hololab_format2_from_files(manifest, staging)
if computed_code_sha != r.code_sha256: HARD_STOP("Code identity mismatch; do not switch sources")

# Do not overwrite an installation; persist source, Release, and verification receipt before atomic placement
persist_verified_receipt(staging, base, r)
atomic_install_without_overwrite(staging, destination_for(r.name, r.version))
```

Installation does not execute the package or install its runtime environment. Handle dependencies and environments separately; never run package scripts before integrity verification. Network failure or a legitimately missing package may follow the caller's mirror policy. Hash mismatch, identity mismatch, and yank must not silently fall back. Mirrors must match the expected authority.

### 12.2 HoloLab FORMAT=2 code identity

This section defines `recompute_hololab_format2_from_files`. Do not substitute a tarball hash or an arbitrary concatenation of file hashes. The registry does not enforce evidence.format, and a 64-character hash alone does not identify an algorithm. Current HoloLab installers use FORMAT=2 below. Other formats require explicit agreement or rejection, never skipped verification.

Let `SHA(bytes)` be lowercase hexadecimal SHA-256. Let `C(value)` be SHA of the UTF-8 bytes of JSON with string-sorted keys, comma/colon separators without extra whitespace, escaped non-ASCII characters, and preserved list order. The exact behavior is Python `json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")`. An approximate canonical JSON implementation with different Unicode escaping or float rendering is insufficient; align cross-language implementations with test vectors. Safely parse the manifest into a string-keyed mapping whose values are losslessly representable in standard JSON, excluding incompatible values such as datetime objects or nonfinite numbers.

1. **Manifest digest.** From the safely parsed original YAML mapping, remove only top-level `version`, `yank`, `yanked`, `yank_reason`, `yanked_at`, `published_at`, and `published_as`. Preserve everything else, including unknown fields, root, cold, and name. Do not fill defaults or replace an unscoped name with its canonical form. Compute `manifest_sha = C(normalized_manifest)`, not a hash of raw manifest file bytes.
2. **Scope.** root defaults to `.` and resolves relative to the selected manifest's directory, within the safe extraction boundary. Use POSIX file paths relative to that tree root. cold defaults to an empty list. Normalize each entry to a POSIX relative path, rejecting absolute paths and `..`. A file is cold if its path equals a cold entry, is below that entry, or the entry is `.`.
3. **Ignore rules and precedence.** Interpret root's UTF-8 `.packignore` using gitignore semantics compatible with GitIgnoreSpec. A child of an ignored directory cannot be restored solely by a negation inside it. Fixed exclusions are `.git`, `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `.DS_Store`, and `.pyc`/`.swp` suffixes. Fixed/ignored directory status propagates to descendants. Root's `.packignore` and the selected manifest, when inside root, are control files assigned to hot first; otherwise precedence is fixed → ignored → cold → hot. **Exclude the selected manifest from the hot tree digest afterward**, because its semantics already contribute separately. `.packignore` still contributes its bytes to the hot tree. Directories themselves do not contribute; fixed/ignored files belong to neither tree.
4. **File-tree digests.** Scan actual hot and cold files separately, rather than trusting evidence digests. A regular file with any execute bit uses ASCII mode `100755`; otherwise `100644`. Sort paths by UTF-8/POSIX filename bytes. Append each record as `mode + " " + relative_path + NUL + SHA(file_bytes)`, where the final hash is 64 ASCII hex characters. **Add no newline or separator between records.** SHA of the concatenation is tree_sha; an empty tree hashes empty bytes. Registry archives forbid symlinks, and installers must reject them too rather than accept link modes to accommodate invalid packages.
5. **Final identity.** Compute `code_sha256 = C([2, hot_tree_sha, manifest_sha, cold_tree_sha])`. Rescan cold files from the archive during installation; do not reuse a publisher's cached baseline. Git commits, times, statistics, and evidence descriptions are not extra inputs to this formula. The hash covers declared scope, not a guarantee that every external dependency is pinned.

**Recorded cross-check vector:** the 343-byte test archive in section 5 was read and recomputed in memory. It has no cold or ignore entries:

```text
hot_tree_sha = 579c7a744ab562386960c543b49b2d8889cef529ff539c6589fc21fc4f813f5c
manifest_sha = 263c8fc3d3f9746b0999dbae2eefcf8b9a0875244a53aff9693fe6f3ee81feaf
cold_tree_sha = e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
code_sha256 = d9507411fd839fd3b4822711ac1f5dfc4526ffbb72ebf0f2764f6249ae525688
```

The result matches the recorded Release. This vector does not replace tests for complex cold/gitignore rules, Unicode, or cross-language number formatting; those branches were reviewed against the client implementation only. The all-zero publication example deliberately demonstrates that the server does not recompute code identity and **cannot pass this installation check**.

## 13. Evidence and verification limits

The documentation review used implementation revision `5b749e8`, covering request models, storage, publication/recovery, archive validation, proxy rules, acceptance scripts, and the Index/Release and package-name contracts. Code identity was additionally checked against HoloLab's FORMAT=2 implementation and installation flow. Readers do not need access to private source files.

| Area | Verification recorded for this document |
|---|---|
| GET /simple/, HEAD /simple/ | Public HTTPS requests, 200; sampled name list was 115 bytes |
| Package JSON and Accept | vnd JSON request returned 200; text/html Accept still returned JSON 200; complete sample package metadata readable |
| GET /index.json | 200; sample was 4177 bytes with 3 releases; Deprecation/Link/no-store checked |
| Archive download | Public GET 200, 343 bytes; archive SHA matched Release; no normal 302 |
| Public /healthz and restricted diagnostics | Public 404 HTML; restricted read returned 200, ready=true/pending=0/releases=3 |
| Missing package, invalid name, unscoped route | Observed 404/422/404; bodies documented above |
| Three write endpoints without tokens | POST requests all returned 401 without writes |
| Disallowed methods | GET /publish and OPTIONS /simple/ returned 403 HTML |
| publish 201, same-key replay 200, duplicate version 409 | Recorded HTTPS acceptance responses from 2026-10-26; no new publication during the documentation review |
| yank/unyank 200 and state changes | Same recorded acceptance session; no package-state changes during the documentation review |
| FORMAT=2 | Read-only in-memory recomputation of the original test archive matched code_sha256; complex scope branches reviewed in code only |
| Rate/quota/concurrency exhaustion, oversize requests, malicious tar, wrong archive hash, COS recovery, unhealthy diagnostics, scope denial | Implementation review only; no production fault injection, quota consumption, or load testing |
| CDN, Range/conditional requests, full installers in other languages | Not verified end to end; no additional behavior promised |

Successful JSON examples preserve recorded response data, with whitespace formatting only. Untriggered errors are explicitly marked as implementation review rather than observations. No secrets, real Bearer tokens, restricted addresses, or internal deployment instructions are included.
