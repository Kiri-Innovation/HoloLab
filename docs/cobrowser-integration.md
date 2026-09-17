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
   Any UI that depends on the API hides itself when this returns
   false — no disabled buttons, no explanatory hints in a regular
   Chrome / Safari tab.

2. **`flops_executor_id` — a machine-specific field on the compute
   node's own `config.yaml`.** *This is not a graph-node property.*
   The value names the Flops device that hosts every artifact the
   node produces, so it belongs at the same level as
   `workspace_root` / `advertised_url` etc. Files touched:

   * `hololab/node/config.py` — `NodeConfig.flops_executor_id: str | None`
   * `hololab/node/runtime.py::_effective_config_view` — exposes it
     over `GET /api/nodes/{id}/config`
   * `hololab/node/runtime.py::_handle_config_set_req` — validates +
     applies it (`str or null`, trimmed, empty→None); persisted to
     `config.yaml` inside the same atomic write path as the other
     editable fields
   * `hololab/protocol/messages.py::Register` — the node sends its
     current value on register so the gateway can cache it in
     `NodeSession.flops_executor_id`
   * `hololab/gateway/registry.py` — cached on `NodeSession`, echoed
     back on `GET /api/nodes`
   * `hololab/gateway/app.py::patch_node_config` — mirrors the
     just-applied value onto the session so a fresh `GET /api/nodes`
     picks up the change without waiting for a reconnect
   * `hololab/frontend/src/canvas/ComputeNodesPanel.tsx` — the
     NodeSettingsDrawer renders a **Flops-only** text input for the
     field under `Advertised URL`; the input is hidden in a regular
     browser

   **The cosmetic allowlist (`_COSMETIC_FIELDS`) intentionally does
   NOT include `flops_executor_id`** — the cosmetic endpoint is for
   graph-node observer state (`preview_open`, `position`), which is
   the wrong level. A test (`test_workflow_cosmetic_patch_rejects_
   flops_executor_id`) asserts a stale client's PATCH is 400'd.

3. **`absolute_path` on handle responses.** `/api/handles/{id}` and
   `/api/handles/{id}/summary` both include the producing node's
   local absolute path. Reason: `window.flops.showDocument` opens a
   *local* file — a proxy URL wouldn't do. The gateway pulls the
   value from the handle row (populated at register time by the node
   runtime); no stripping / rewriting.

## Data-flow summary

```
operator opens right-side COMPUTE NODES panel
       │
       ▼ clicks ⚙ on "kiri4090"
NodeSettingsDrawer  →  PATCH /api/nodes/{id}/config
       │                       │
       │                       ▼
       │              gateway forwards over WS
       │                       │
       │                       ▼
       │              node validates + writes config.yaml
       │                       │
       │                       ▼
       │              gateway mirrors onto NodeSession
       │
       │ later:
       ▼
node produces artifact
       │ registers handle (path=/cloud/…/j/…/output.splatv)
       ▼
gateway.handles
       │
       ▼ GET /api/handles/{id}
frontend receives HandleInfo.absolute_path + node_id
       │
       ▼ stored on PreviewTarget
AlgorithmNode drawer
       │
       ▼ user clicks "↗"
OpenInCocoderButton
       │  looks up computeNode = computeNodesById[target.node_id]
       │  → deviceId = computeNode.flops_executor_id
       │
       ▼ window.flops.showDocument({ path, deviceId })
Cobrowser host ── confirmation card ── Cocoder tab opens file
```

## User flow

1. Open a workflow in Flops Cobrowser (`http://…:8828/#w=…`). The
   `flopsAvailable()` check returns true, so:
   * the NodeSettingsDrawer shows an extra **Flops executor id**
     field;
   * the preview drawer + zoom overlay will render a `↗` button
     when a handle finishes loading (see below).
2. Operator opens the right-side **Compute nodes** panel → clicks the
   ⚙ on `kiri4090` → fills the **Flops executor id** field
   (`dev_kiri4090xxx`) → clicks **Apply**. The value is written to
   the node's `config.yaml` and mirrored onto the gateway session.
3. Run the workflow. When the node reaches `done`, the preview
   drawer shows a `↗` button in its header (next to the `⧉` copy
   button). If the video-array-source is zoomed, the zoom overlay
   also shows one on the top-right, next to the filename pill.
4. Clicking `↗` looks up
   `computeNodesById[handle.node_id].flops_executor_id` at that
   instant and calls `window.flops.showDocument({ path: <absolute
   path>, deviceId: <that value> })`. The Flops host shows its
   confirmation card. Once the user picks *allow*, Cocoder opens the
   file. The button flashes `✓`.

If the producing compute node has no `flops_executor_id` yet, the
`↗` click does NOT hit the API. Instead the button expands a
**visible guide callout** anchored below itself: *"Set the Flops
executor id first — kiri4090 has no `flops_executor_id`
configured. Open the right-side **Compute nodes** panel → click the
⚙ on **kiri4090** → fill the **Flops executor id** field →
**Apply**."* The callout auto-dismisses after ~8 s. Tooltip-only
feedback (previous behaviour) was easy to miss.

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
  require the operator to pin `flops_executor_id` on the compute
  node.
* **First-open is a 1.5 s pause.** The Cobrowser spec delays every
  negative response on an unauthorised origin by ≥ 1.5 s to prevent
  path probing. That means the *first* click on the button (before
  the user promotes the HoloLab origin to "always allow") shows a
  brief `…` state. Once the origin is authorised, clicks are
  instant.
* **iframe scenarios.** `window.flops` is top-frame only per spec.
  Anyone embedding HoloLab inside another page won't see the button.
* **No history migration.** Compute nodes' `config.yaml` files
  don't have the field until the operator sets it; the frontend
  defaults to hiding the button (or showing the guide) until then.
  Nothing to backfill.

## Jump to source

A second Cobrowser-integrated button lives on the **canvas node header
itself** (next to `⧉` and the preview caret): a code-icon
`</>` "Jump to source". Clicking it opens the pack's implementation
script in Cocoder rather than a produced artifact.

Target resolution:

1. If the pack manifest declared **`source_entry`** — a path relative
   to the Kiri4DGS workspace root, or absolute — that is the target.
   Configured for the Sharp-4DGS / STG packs so a click jumps straight
   to the code:

   | pack | `source_entry` |
   |---|---|
   | `video-to-camera-track` | `sharp-4dgs/per-frame/video_to_colmap.py` |
   | `track-to-gs-sequence`  | `sharp-4dgs/per-frame/colmap_to_3d.py` |
   | `video-to-colmap`       | `sharp-4dgs/per-frame/video_to_3d.py` |
   | `gs-seq-to-multiview-colmap` | `sharp-4dgs/per-frame/gsseq_to_multiview.py` |
   | `stg-train`             | `SpacetimeGaussians/train.py` |
   | `stg-to-splatv`         | `Utils/STG_to_SplaTV/convert_to_splatv_lite.py` |

2. Otherwise, `{packs_dir}/{name}@{version}/manifest.yaml` — the exec
   orchestration lives there and is always present. Source packs
   (`single-video-source`, `video-array-source`, demo packs) use this
   fallback; there is no separate implementation script.

`packs_dir` is exposed on `GET /api/nodes` (already visible read-only
in the NodeSettingsDrawer). The compute node is picked in this order:
graph node's explicit `assigned_node_id` → first entry of the pack's
`node_ids` (i.e. first online node offering this pack).

Same gating + guide callout as the `↗` button — hidden in a regular
browser, and the click on a compute node without `flops_executor_id`
expands the same guide instead of firing the API. See
`hololab/frontend/src/canvas/OpenSourceButton.tsx` and
`FlopsExecutorGuide.tsx`.

## Related surfaces

* Node config schema: `hololab/node/config.py::NodeConfig`
* Node config get/set: `hololab/node/runtime.py::_effective_config_view`,
  `_handle_config_set_req`
* Register frame: `hololab/protocol/messages.py::Register`
* Gateway session cache: `hololab/gateway/registry.py::NodeSession`
* Gateway PATCH mirror: `hololab/gateway/app.py::patch_node_config`
* Frontend Flops helper: `hololab/frontend/src/flops.ts`
* Button component: `hololab/frontend/src/canvas/OpenInCocoderButton.tsx`
* Jump-to-source button: `hololab/frontend/src/canvas/OpenSourceButton.tsx`
* Shared guide callout: `hololab/frontend/src/canvas/FlopsExecutorGuide.tsx`
* Manifest field: `hololab/manifest/schema.py::Manifest.source_entry`
* Settings UI: `hololab/frontend/src/canvas/ComputeNodesPanel.tsx::NodeSettingsDrawer`
* API spec source: `temp/cobrowser-web-api.md`
