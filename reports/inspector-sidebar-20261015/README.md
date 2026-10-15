# Inspector sidebar review — 2026-10-15

The canvas now uses a 380px right inspector (resizable from 320–400px), with compact, left-aligned parameter fields and collapsible descriptions. The bottom dock is removed. Compute assignment, live pulse and node settings share one section above it. With no selection, Recent Jobs fills the sidebar. Snapshot run history remains available in an expandable section with its existing Refresh action.

Parameter and assignment patches still flow through onInspectorChange → controlled graph → useDraftAutosave → POST /api/workflows. Single-node run/stop and job updates retain their existing handlers. Delete now shares the controlled deletion path, which also removes deleted nodes from the catalog recovery cache: previously that cache could resurrect a deleted node and revert edited parameters.

Changed implementation files:
- `hololab/frontend/src/App.tsx`: sidebar composition and deletion cache fix.
- `hololab/frontend/src/canvas/NodeInspector.tsx`: compact fields, left alignment, collapsible help and About modal.
- `hololab/frontend/src/canvas/ComputeNodesPanel.tsx`: unified assignment/pulse/settings section.
- `hololab/frontend/src/canvas/NodeMetricsPanel.tsx`: reusable settings entry and narrow header.
- `hololab/frontend/src/canvas/inspector.css`: theme-token layout and scrolling.
- `hololab/frontend/src/canvas/useResizableLayout.ts`: absent localStorage values now use the intended default rather than the minimum.
- `hololab/frontend/e2e/inspector-sidebar.spec.ts`: screenshots, selection, scrolling, autosave, assignment, deletion, run-history refresh.
- `hololab/frontend/e2e/resource-lens-scroll.spec.ts`: verifies the new narrow stacked layout, scrolling and restored focus.

## Actual browser screenshots

These are unmodified Playwright screenshots of production builds, with deterministic intercepted API fixtures (one demo pack, one compute node, one job). They are not screenshots of Steven's live workflow. Before was built from e512869; after was built from this change. Both served by local Vite preview. All eight images were opened and visually reviewed.

| State | Before | After |
| --- | --- | --- |
| Unselected, light | [before](before-unselected-light.png) | [after](after-unselected-light.png) |
| Selected, light | [before](before-selected-light.png) | [after](after-selected-light.png) |
| Unselected, dark | [before](before-unselected-dark.png) | [after](after-unselected-dark.png) |
| Selected, dark | [before](before-selected-dark.png) | [after](after-selected-dark.png) |

## Validation and deployment

- `npm run build -- --outDir /tmp/hololab-inspector-after` passed (existing bundle-size advisory only).
- Nine Playwright tests passed across inspector-sidebar, inspector-batch-size, node-run-control, canvas-live-progress, resource-metrics and resource-lens-scroll. The new sidebar test ran against the production preview on 4175; existing tests used Vite on 5173.
- Verified autosave payloads for edited parameters, assigned node and deletion; tested run-history Refresh, single-node run/stop, live progress, four resource lenses and mobile resource scrolling.
- Published the verified build to `hololab/frontend/dist`, copying assets first and replacing index.html last. Existing assets retained for already-open tabs. HTTP index and JS/CSS bytes on 8828 matched the verified build.
- Gateway PID 779985, started 2026-10-15 07:58 UTC, remained unchanged. No gateway restart and no Difix execution.
- The specified WIP files were not edited or staged. Pre-existing untracked files were left untouched.
