# Pack discovery without service restarts — 2026-11-05

## Result

| Actual manifest identity | GET /api/packs | Local CLI schema validation |
|---|---|---|
| builtin/interval-pick@0.1.0 | present, online=true | passed |
| col4d/repeat-4d@0.1.0 | present, online=true | passed |
| vista4d/colmap4d-render-video@0.1.0 | absent | passed |
| k4d/vista4d-generate@0.1.0 | absent | passed |

Evidence: `packs-before.json`, `packs-after.json`, `manifest-validation.txt`.
These are filtered actual API responses, not synthesized success records.
Node: KiriA800 (`3038e635-4840-46dd-aa0c-6bd9c477b852`).

## Discovery and effective configuration

Read-only calls used: GET `/api/overview`, `/api/nodes`, `/api/packs`,
`/api/nodes/3038e635-4840-46dd-aa0c-6bd9c477b852/config`, `/openapi.json`, `/`.
The config endpoint reads the live node's in-memory configuration.
`live-config.json` preserves its pack_dirs. `nodes-before.json` preserves the
scan roots advertised to the gateway, including the automatically included
installed_packs root. Existing configured roots are:

- `/cloud/cloud-ssd1/Kiri4DGS/SpacetimeGaussians/packs`
- `/cloud/cloud-ssd1/Kiri4DGS/Utils/STG_to_SplaTV`
- `/cloud/cloud-ssd1/Kiri4DGS/packages/FreeTimeGsVanilla/packs`
- `/cloud/cloud-ssd1/.hololab/node/local-packs`
- `/cloud/cloud-ssd1/Kiri4DGS/hololab/community_packs`
- `/cloud/cloud-ssd1/Kiri4DGS/packages/BiRefNet`
- `/cloud/cloud-ssd1/Kiri4DGS/tryouts/mega-sam/packs`

`packages/Vista4D/packs` is absent. repeat-4d is already covered by the
community root. Builtins are loaded separately before user roots by
`hololab/node/runtime.py::_load_packs`, via
`hololab/bundled.py::load_bundled_packs`. The bundled index and content hashes
are checked; adding a directory alone is insufficient. interval-pick is
already indexed and advertised. Existing watcher rescans load bundled packs
as well as configured roots.

## Why PATCH was not sent

The advertised endpoint is PATCH
`/api/nodes/3038e635-4840-46dd-aa0c-6bd9c477b852/config`, body
`{"patch":{"pack_dirs":[...existing configured roots...,"/cloud/cloud-ssd1/Kiri4DGS/packages/Vista4D/packs"]}}`.

However, `hololab/node/runtime.py:1714` checks
`if roots_changed or pack_dirs_changed` and calls `_restart_file_server()`.
That method at line 621 stops and starts the file-server child process.
Only afterwards does it rescan/restart the watcher. Git blame identifies the
pack-roots condition as commit `de91c611` dated 2026-10-30, predating the live
node connection on 2026-11-05. Thus this is not merely an uncommitted future
implementation change.

The user's prohibition covers **any service restart**. No PATCH was sent;
there is no PATCH success response to report. No config.yaml edit, symlink
workaround, package copy, node/gateway/file-server restart or job operation was
performed. Vista4D discovery awaits a maintenance window permitting the API's
file-server restart, or a future truly restart-free handler. A full node or
gateway restart is not inherently needed just to discover these packs.

## Frontend

Skipped per instruction: both connectionValidation.ts and
connectionValidation.test.ts have uncommitted changes, with new
arrayViewConnections.ts / arrayViewCompatibility.json files. See
`frontend-status.txt`. No build, dist replacement or backup was made by this
session. Observed online bundle: `index-BNcwfaOo.js`, CSS `index-CGdLYlzW.css`;
see `frontend-before.json` and `frontend-observed.json`. Another session may
change deployment independently.

## Deferred verification

After an authorized maintenance window / backend code reload:

1. Add Vista4D/packs through live config API (re-read current roots first),
   verify both Vista4D packs online via `/api/packs` and `/api/pack-catalog`.
2. Verify new array-view compilation/prepare and real gateway dispatch with
   the updated backend. Discovery alone is not execution compatibility.
3. Verify each pack's runtime environment/dependencies and downstream
   end-to-end execution on available resources. No GPU job was dispatched.
4. Build/deploy frontend wiring only after its owning session commits it;
   retain a timestamped dist backup and verify the served bundle.

Only report files were added in this turn. Existing jobs and other sessions'
files remain untouched. No push. HoloLab operational guidance used:
`skill/SKILL.md` (read-only inspection; the restart prohibition overrides any
restart suggestions in repository documentation).
