# Follow-up: actual restart-free reload attempt

## Exact cause and API results

Live GET `/api/nodes/3038e635-4840-46dd-aa0c-6bd9c477b852/config`
confirms Vista4D/packs is absent (`live-config.json`).

- `hololab/node/runtime.py:670`: manual reload calls `_load_packs` with
  **in-memory** `self._config.pack_scan_dirs` only.
- `hololab/node/runtime.py:703`: watcher snapshots the configured roots when
  its watch task starts; line 713 watches those roots recursively. Changes
  trigger the same reload routine; it does not discover new outside roots.
- `hololab/node/packs.py:217`: scan only the declared pack source's explicit
  packs container / supported pack directory layouts, not arbitrary repos.
- `hololab/node/runtime.py:1657`: pack_dirs is indeed editable via config-set.
  But lines 1714–1720 restart the file-server child when pack_dirs changes.
  Therefore PATCH is available but violates the no-service-restart constraint.
  No config PATCH or direct YAML edit was performed.

Called the real gateway endpoint defined at `hololab/gateway/app.py:1948`:

```http
POST /api/packs/reload?node_id=3038e635-4840-46dd-aa0c-6bd9c477b852
HTTP 200
```

```json
{"nodes":[{"node_id":"3038e635-4840-46dd-aa0c-6bd9c477b852","count":88,"manifest_changed":false}]}
```

This request rescans/re-advertises without invoking the config handler or
restarting services. `reload-response.json` is the actual response.
`packs-after.json` and `catalog-after.json` confirm interval-pick and repeat-4d
remain visible; both Vista4D packs remain absent. The rescan succeeded; it
cannot compensate for a missing scan root.

## Relocation fallback — assessed, NOT executed

Both Vista4D manifests declare `root: ../..` (render manifest line 7,
generate manifest line 7). Both adapters use
`ROOT = Path(__file__).resolve().parents[2]` (render run.py line 18,
generate run.py line 9) and resolve Vista4D utilities / checkpoints below it.
Moving just the two pack folders into community_packs or local-packs would
make the package root and imports/checkpoints wrong.

A deployment under an already scanned root is technically possible if the
complete required package-root layout is retained, with local dependencies
and checkpoints resolved, or the owning session explicitly changes the
packaging/entrypoints. The supported scanner's scope/name nesting can locate
the two manifests, but discovery alone does not validate execution paths.
This needs package-content identity and dependency checks before deployment.
A path move does not inherently change name@version, but changed manifest
or content scope may change the code identity; duplicate identities across
roots also have first-wins implications. No folders, manifests, symlinks or
weights were moved/copied/edited here.

## Frontend and deferred checks

Frontend wiring remains uncommitted (including App.tsx); see
`frontend-status.txt`. Build/deploy skipped. No dist backup created.
`frontend.json` records the observed bundle, index-BS5L_gA9.js. It changed
from the prior turn independently; this session did not deploy it.

Deferred items are distinct:

1. Vista4D source registration: permit the config API's **file-server child
   restart** in a maintenance window, or implement/load a genuinely
   restart-free config handler. Full node/gateway restart is not inherently
   required for pack discovery. Alternatively review the relocation design.
2. New backend array-view prepare / execution behavior: requires loading
   the new backend code; validate real gateway dispatch only afterwards.
3. Runtime environments, GPU availability, render/generate integration:
   not verified by catalog/schema checks and not inherently restart-dependent.
4. Frontend deployment: waits for the other session's commit, not a restart.

No services restarted, jobs manipulated, configs hand-edited, pack sources
modified, or code pushed. Only these evidence files are committed.
