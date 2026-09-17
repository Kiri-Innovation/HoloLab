# Flops Cobrowser integration — "Open in Cocoder"

HoloLab detects when its page is rendered inside the Flops built-in
browser (Cobrowser) and adds an "Open in Cocoder" button to preview
drawers + zoom overlays. The button hands the artifact's local
absolute path to the Flops host, which opens it in the Cocoder
workspace pane. See the API spec at `temp/cobrowser-web-api.md` (the
canonical version lives inside the Flops repo) for the exact
`window.flops.showDocument` shape.

## What ships

Three layers, no changes to the runtime data plane:

1. **`useFlopsEnv` / `flopsAvailable()`** in `hololab/frontend/src/flops.ts`.
   Checks `window.flops.version === 1 && typeof showDocument === "function"`.
   Any UI that depends on the API hides itself when this returns false —
   no disabled buttons, no explanatory hints. Regular Chrome / Safari
   don't get a broken affordance.

2. **`flops_executor_id` — a new cosmetic field on `GraphNode`.**
   Wire type `wire.ts::GraphNode.flops_executor_id`, model
   `hololab/gateway/workflows.py::GraphNode`, response
   `hololab/gateway/models.py::GraphNodeOut`, cosmetic allowlist
   `hololab/gateway/app.py::_COSMETIC_FIELDS`. The user pins this in
   the NodeInspector; the value is the Flops device id where the
   node's artifacts land (typically the compute node's `dev_xxxxxxxx`
   identifier). Cosmetic in the strict V8 sense: does not affect
   dispatch or lineage identity, so patching it does NOT fork a
   snapshot. Autosave persists it on the draft; the cosmetic PATCH
   endpoint mirrors it onto the workflow's newest snapshot so the
   "last run" view stays in sync.

3. **`absolute_path` on handle responses.** `/api/handles/{id}` and
   `/api/handles/{id}/summary` both include the producing node's
   local absolute path. Reason: `window.flops.showDocument` opens a
   *local* file — a proxy URL wouldn't do. The gateway pulls the
   value from the handle row (populated at register time by the node
   runtime); no stripping / rewriting.

## Data-flow summary

```
node produces artifact
       │
       ▼ registers handle
gateway.handles ─── path = /cloud/…/w/wf/j/j/model.splatv
       │
       ▼ GET /api/handles/{id}
frontend receives HandleInfo.absolute_path
       │
       ▼ stored on PreviewTarget
AlgorithmNode expand drawer
       │
       ▼ user clicks "↗"
OpenInCocoderButton
       │
       ▼ window.flops.showDocument({ path, deviceId })
Cobrowser host ── confirmation card ── Cocoder tab opens file
```

## User flow

1. Open a workflow in Flops Cobrowser (`http://…:8828/#w=…`). The
   `flopsAvailable()` check returns true, so the NodeInspector shows
   the extra **Flops executor id** field.
2. User enters the id for the compute node the artifact will land
   on (`dev_...`). Autosave stores it on the draft; a cosmetic
   PATCH mirrors it onto the workflow's latest snapshot.
3. Run the workflow. When the node reaches `done`, the preview
   drawer shows a `↗` button in its header (next to the `⧉` copy
   button). If the video-array-source is zoomed, the zoom overlay
   also shows one on the top-right, next to the filename pill.
4. Clicking `↗` calls `window.flops.showDocument({ path: <absolute
   path>, deviceId: <configured id> })`. The Flops host shows its
   confirmation card. Once the user picks *allow*, Cocoder opens the
   file. The button flashes `✓`.

If the user hasn't configured `flops_executor_id` yet, the `↗`
click surfaces an inline error: *"set flops_executor_id in node
settings"*. No API call is made.

## `reason` code humanisation

The button maps `showDocument` result reasons to short strings so a
tooltip / flash makes sense to the user:

| `reason` | User-visible string |
|---|---|
| `declined` | cancelled |
| `busy` | another request is waiting for confirmation |
| `timeout` | confirmation card timed out |
| `invalid-path` | invalid path (bug) |
| `forbidden` | page origin cannot use this API |
| `not-found` | path not found on device |
| `outside-roots` | path is not inside a Cocoder workspace root |
| `unsupported-kind` | path is neither file nor directory |
| `unknown-window` | tab was closed |
| `unavailable` / `bad-response` / `error` | internal error |

Unauthorised origins never see anything besides `declined` per the
spec's anti-probing rule — the `not-found` / `outside-roots` /
`unsupported-kind` rows are for grants the user promoted to "always
allow this origin".

## Non-goals & known trade-offs

* **No `deviceId` inference.** Cobrowser's default ("this tab's
  device") is the Mac the user is running Flops on — not the compute
  node where the artifact lives. We refuse to guess and instead
  require the user to pin `flops_executor_id` explicitly.
* **First-open is a 1.5 s pause.** The Cobrowser spec delays every
  negative response on an unauthorised origin by ≥ 1.5 s to prevent
  path probing. That means the *first* click on the button (before
  the user promotes the HoloLab origin to "always allow") shows a
  brief `…` state. Once the origin is authorised, clicks are
  instant.
* **iframe scenarios.** `window.flops` is top-frame only per spec.
  Anyone embedding HoloLab inside another page won't see the button.
* **No history migration.** Existing workflows have
  `flops_executor_id: null`. The user configures it on-demand; there
  is no bulk import path.

## Related surfaces

* Backend cosmetic allowlist: `hololab/gateway/app.py::_COSMETIC_FIELDS`
* Frontend Flops helper: `hololab/frontend/src/flops.ts`
* Button component: `hololab/frontend/src/canvas/OpenInCocoderButton.tsx`
* Field UI: `hololab/frontend/src/canvas/NodeInspector.tsx::FlopsExecutorIdField`
* API spec source: `temp/cobrowser-web-api.md`
