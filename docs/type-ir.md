# Port type IR: phased unification

Status: phases 1–5 implemented. Frontend outlets use explicit adapters and a
terminal IR renderer. Backend normalization now supplies both compatibility and
additive versioned API fields; runtime observations carry scope/evidence.
Existing YAML and legacy response field semantics remain unchanged.
Sources: `hololab/frontend/src/types/portTypeIR.ts`,
`hololab/gateway/port_type_ir.py`, and `hololab/gateway/type_observation.py`.

## Contract

Type meaning must never be derived from a formatted label. A renderer is a
terminal consumer, not an input to wrapping, unwrapping, validation or scheduling.
There is no new textual type language. Existing YAML fields remain the contract.
Tags and dimension labels are opaque identifiers, including the known wildcard
`any`; unresolved type information is a distinct `unknown` variant.

Three separate records live in the versioned `PortTypeIR` envelope:

- `declaration`: original manifest fields, including inheritance directives.
  This preserves author intent; it is not automatically the invocation type.
- `views`: normalized invocation and aggregate types, with explicit participation.
  Aggregate denotes the value on the wire. A broadcast wire is one whole value,
  not a materialized collection of N copies.
- `runtime`: handle observations. Measurements never overwrite a declaration or
  prove compatibility. There is no timestamp or duration invented from a count.

The JSON representation has `schemaVersion: 1`. Each semantic type is recursive:

```ts
type TypeExpr =
  | { kind: 'unknown'; reason: string }
  | { kind: 'object'; tags: string[]; contentAxes: { label: string | null }[] }
  | { kind: 'array'; axis: { label: string | null }; element: TypeExpr };
```

An array constructor always means a collection layer. `contentAxes` describe
intrinsic axes of an object, not shard-enumeration layers. Null denotes an unnamed
axis, never a fabricated name. Measurements such as pose/intrinsic counts do not
become content axes without a semantic source establishing that interpretation.
An array of unknown elements remains a known array with an unknown leaf.

For example, a **normalized** invocation value of type `array(image)` becomes
`array(array(image))` when mapped. A broadcast `colmap-cams` remains `colmap-cams`
in both views. This says nothing about how many frame shards exist; that is an
observation, not something a type wrapper may fabricate.

### Important legacy boundary

Do not pass `resolved_outputs` or raw `PortSpec` into `viewsFromInvocation`.
`InputSpec.dim_labels` already documents aggregate axes in some modes; the
existing effective-arrayed rule is an OR operation, not unconditional recursive
wrapping. A scalar output can be aggregated, while a scalar input is broadcast.
Blindly converting these fields and wrapping again repeats the current bug.

Phase 2 provides explicit source-aware frontend adapters (below); phase 3
centralizes backend normalization for validation and API serialization. Reuse `effective_port_arrayed`, `effective_output_tags` and
`effective_output_dim_labels` rather than silently inventing new pack semantics.
When legacy metadata cannot distinguish collection and content axes, retain
unknown provenance/role and do not guess from a display string or label spelling.
Neither the word `frame` nor the presence of `dim_sizes` proves a fan-out boundary.

## Pure operations

| Function | Input / result | Rule |
|---|---|---|
| `objectType(tags, contentAxes?)` | structured identifiers → `TypeExpr` | Copies input lists; empty tags become unknown; `any` stays a known wildcard. |
| `unknownType(reason)` | reason → `TypeExpr` | No scalar or array assertion. |
| `wrapArray(element, axis?)` | `TypeExpr` → `TypeExpr` | Adds exactly one collection layer. |
| `wrapArrays(element, axes)` | outer-first axes → `TypeExpr` | Adds any finite number of layers, preserving order. |
| `unwrapArrays(type, depth=1)` | `TypeExpr` → `TypeResult` | Removes collection layers only; reports unknown/non-array/invalid depth rather than silently succeeding. Failure returns original input, no partial conversion. |
| `invocationFromAggregate(type)` | `TypeExpr` → `TypeResult` | Removes exactly one known collection layer. Not for broadcast. |
| `broadcastInput(type)` | `TypeExpr` → `TypeViews` | Invocation and aggregate are the same whole value, even if already an array. |
| `viewsFromInvocation(type, participation, axis?)` | normalized invocation → `TypeViews` | `none` unchanged; `map-input`/`collect-output` wrap once; `broadcast-input` unchanged. |
| `unknownRuntime(handleId, reason)` | handle identity → `RuntimeShape` | Missing observations, not zero counts or a known scalar. |

Functions are immutable, dependency-free at runtime and have no DOM or network
access. Structural sharing is intentional; consumers treat IR values as readonly.
These are semantic primitives, not replacements for the current compatibility
policy. No new compatibility checker or display formatter is introduced in phase 1.

## Observation model

Each observed field uses `Observation<T>`:

```ts
{
  evidence:
    | { status: 'unknown'; reason: string }
    | { status: 'sampled'; value: T; source: string; sample: string }
    | { status: 'complete'; value: T; source: string };
  legacyValue?: T;
}
```

`complete` is scoped to the referenced handle and field, not all future runs or
all objects in the workflow. A representative first-element pose count is
`sampled`, with a sample identifier. `dim_sizes` alone does not establish coverage.
The optional `legacyValue` field remains accepted by the versioned wire model.
The canvas no longer reconstructs or displays it: unknown evidence is unmeasured,
even if old numeric fields are present. API legacy fields themselves remain unchanged.
Zero is a real measured value. Unknown shape differs from measured scalar `[]`.

`RuntimeShape.axes` is outer-first. Each axis records its role
(`collection` / `content` / `unknown`) and size evidence. Overall axis-list
coverage and individual size coverage may differ. Internal counts have independent
evidence; e.g. a complete outer directory count and a sampled pose count coexist.
No measurement is automatically replicated to every element when a type is
wrapped or broadcast. Phase 4 records these scopes at the actual probe boundary.

The frontend validates `runtime_shape` at the HTTP/cache boundary in
`runtimeObservation.ts`. Unsupported versions or malformed evidence fall back to
legacy fields with unknown coverage. Python uses Pydantic discriminated unions
for the same shape. Unknown axis labels remain JSON `null`; absent measurements
are not serialized as fabricated numeric values.

## Backend / API implementation (phase 3)

`port_type_ir.py` defines recursive `object` / `array` / `unknown` variants,
`Declaration`, `TypeViews`, `RuntimeShape`, and the versioned envelopes.
`workflows.resolved_port_types` is the shared normalizer. It invokes the existing
output resolvers, preserves their old projection exactly, and creates IR once.
`validate_snapshot` compares aggregate IR through `compatible`; error messages
use `render_type`, including named axes. The public `ports_compatible` legacy
signature delegates to this structured compatibility operation too.

Additive fields:

```ts
// Workflow/snapshot graph node, and /api/resolve graph-node current_draft:
type NodePortTypes = {
  schemaVersion: 1;
  inputs: Record<string, PortTypeIR>;
  outputs: Record<string, PortTypeIR>;
};
// graphNode.port_types?: NodePortTypes
// current_draft.port_types?: NodePortTypes
```

`resolved_outputs` still has exactly `{tags, arrayed, dim_labels}` per output.
`port_types` is accepted on a graph round trip but excluded from persistence.
Without a pack catalog it is unavailable. Graph drafts explicitly carry unknown
`runtime`; historical counts are not attached to draft contracts. Read the
associated `latest_run.output_handles` or handle summary for observations.

One intentional validation correction: derived output dimensions now determine
compatibility just as they already determined `resolved_outputs` (e.g. a
`get-index` output retaining a second input dimension is still an array). This
changes neither the old fields nor scheduling, but can reject edges whose
incompatible derived dimensions were formerly missed by static-label checking.
No runtime count becomes a compatibility constraint.

The whole-workflow run route uses `packs_by_key_from_catalog` too; its old local
copy omitted dimension derivation fields. Both actual run validation and graph
GET/resolve now receive the same catalog representation.

Backend invocation normalization uses `enumeration_depth`, extracted unchanged
from the executor into the pure IR module. Execution calls the same function and
keeps its existing `WorkflowRunError` contract. The normalizer merges parameter
defaults exactly as dispatch does and `shard_view` removes all enumerated directory
layers, not blindly one layer. On inputs, explicitly retained content axes become
object `contentAxes`. Collected output projection uses the common driving-input
depth. Invalid/unresolved depth or too few aggregate layers yields `unknown`;
legacy aggregate fields and compatibility checks are still preserved. Tests cover
nested directories, content-axis subtraction, parameter defaults and invalid
values. The TS one-layer pure helper is still valid for a normalized one-boundary
operation; it is not a substitute for the backend's physical shard context.

The frontend keeps its explicit legacy contract adapter for unsaved drafts and
older services. Backend `port_types` is available to API consumers; this release
does not make the canvas depend on a saved server response for every draft edit.
Both renderers use `arrayed[frame]<T>` rather than the old `arrayed<frame> of T`.
There is no display-string parsing or wrapper-string inference.

## Runtime observation implementation (phase 4)

Handle summary and inline `latest_run.output_handles[port]` add:

```ts
// Other existing fields remain alongside this additive envelope.
runtime_shape?: RuntimeShape & { schemaVersion: 1 };
```

`type_observation.observe_summary` runs where `handle_summary` knows the actual
probe scope, before cache serialization. `internal_count_items` has a typed
`InternalCountItem {label: string, value: number}` model without changing the
legacy JSON shape. Full `internalCounts` carry independent evidence.

- Registered handle tags: complete **metadata** scope, source explicitly named;
  not a filesystem validation of that tag's semantic correctness.
- Directory dimensions: complete directory-tree scan when uniform.
- Tag content dimensions: sampled when read at one child, complete when the
  referenced handle root itself is the whole probe scope.
- Internal counts: sampled at the first leaf, or complete at the root scope.
  `sample` is relative to the handle, never an absolute host path.
- Missing/ragged shape: unknown. Old numbers are preserved but not upgraded.

A shape may have a sampled overall axis-list and a complete outer size plus a
sampled inner size. `complete` describes coverage of the field's measurement,
not independent verification of file-header claims. Probes remain cheap; this
phase does not scan every element's internal contents.

The summary cache keys by `(handle_id, dim_labels)` so an early request without
resolved labels cannot poison later contextual evidence. Tombstoning invalidates
all scopes for that handle. The frontend summary cache validates the envelope;
`runtimeFromSummary` accepts validated evidence and rejects a mismatched handle id.
Missing or unsupported `runtime_shape` yields `unknownRuntime`; no legacy axes or
counts are backfilled. Element-row projection slices known observation structure
and retains its coverage/source/sample; unknown evidence remains unknown. UI layout
and tooltip captions are unchanged.

## Coverage of the five display outlets (phase 2 inventory)

| Outlet | Current source | IR data needed |
|---|---|---|
| Port circle/text and edge tooltip | `typeTooltip.ts`, `PortHandle.tsx`, `TypedEdge.tsx` | Port identity, declaration + normalized views, participation, runtime axes/counts and evidence. UI captions such as `after arrayed` or `arrayed (auto-popped)` are renderer decisions, not type values. |
| Compact edge chip | `TypedEdge.tsx`, `formatTypeLabel` | Effective wire type plus observed axes/counts; tag abbreviation is a final rendering option. |
| Edge inspector | `EdgeInspector.tsx`, `formatTypeLabelLong` | Same effective type, full tags/axes, available observations. No separate `of` grammar. |
| Snapshot edge labels | `SnapshotCanvas.tsx` | Same model associated with historical snapshot/handle context; no substitution with current draft. |
| Incompatibility messages (frontend and backend) | `App.tsx`, `workflows.py` | Structured source/target effective types plus compatibility reason; count observations are not declaration constraints. |

These outlets need no new display-string parser. Identity, snapshot context and
compatibility reasons remain outside `TypeExpr`; the existing caller already owns
them. The IR carries the types and observations, not every UI concern.

## Acceptance and remaining stages

Phase 1 tests cover the Cartesian product of eight type shapes and four
participation modes, round trips across 0/1/2/3/8 collection layers, unknown
subtrees, wildcard and multi-tag objects, immutable input, invalid pop depth,
content axes not being popped, JSON serialization, and all three observation
statuses including zero. No UI wiring means existing production behavior is
unchanged; run existing UI regression tests and frontend build as confirmation.

Phases 1 and 2 were accepted separately. Phases 3–5 were then authorized as a
continuous run, with separate commits:

3. Python IR, shared normalized validation/API result, versioned additive payload,
   legacy field/round-trip and HTTP resolve compatibility tests.
4. Scoped runtime evidence, typed counts, frontend boundary validation and legacy
   fallback, plus browser regressions of the five display outlets.
5. Pack documentation of dimension rules, broadcast exceptions and the distinction
   between declaration, effective views and runtime observations; no pack changes.

Acceptance: phase 3's focused backend suite passed 226 tests, plus 3 resolve
regressions. Phase 4's expanded suite passed 247 tests plus 30 additional
propagation/API/bundle tests; frontend unit tests passed 356, browser regressions
16 (light/dark, 50/100/150%, port/edge/title/status tooltips, wiring/selection and
snapshot/inspector). The production frontend build regenerated dist. A subsequent
phase-3 hardening commit passed 257 tests covering actual run-route validation,
shared multi-layer shard depth, the scheduler, observations and API compatibility.

### Remaining boundaries / cross-client coordination

- Existing Flops/agent clients can keep reading legacy fields. To distinguish
  sampled counts they must adopt version-1 `runtime_shape`; no mandatory coordinated
  migration or service restart was performed by this change.
- The existing frontend tooltip's element row removes one outer observation
  layer by design. Backend `port_types.views.invocation` includes physical shard
  depth; consumers needing dispatch semantics must use that field rather than
  parse a tooltip. Missing/invalid driving metadata stays `unknown`.
- Legacy dimension resolution fallbacks (including unresolved generic axes) are
  preserved, not promoted into proof of complete dimension knowledge.
- Snapshot card mode forwarding was subsequently fixed: see the snapshot note below.


## Phase 2 implementation and acceptance

`legacyPortType.ts` is the sole bridge for the display migration:

- `typeFromLegacy` accepts effective `EdgeType` fields, including the fields
  already sourced from backend `resolved_outputs`. An array flag constructs
  collection layers (unnamed legacy arrays retain one layer); scalar axes are
  object content axes, not automatic collection promotion.
- `declarationType` reads raw `PortSpec` fields. It does not apply `tags_from` or
  reimplement graph inference. The left tooltip declaration remains author intent;
  the effective right-hand view uses the already-resolved fields instead.
- `legacyViews` requires explicit `invocation` or `aggregate` provenance. Only an
  invocation source can be wrapped; an aggregate source is reused and one known
  collection layer is removed to obtain the invocation view. Broadcast reuses
  the whole type, including an existing array, without introducing a dimension.
- Production `adaptPortType` always receives **effective aggregate** fields from
  the existing helpers/resolver. In particular `arrayed: true` plus a node toggle
  does not cause another array layer: current pack semantics use OR, not an
  additional wrapper. This corrects the previous tooltip's unconditional shell.
- The phase-2 numeric fallback has since been removed (see the policy below).
  `runtimeFromSummary` now uses only structured evidence. `elementRuntime`
  preserves the third-row element projection and observation provenance;
  this display row is not a per-shard verification API.

`renderPortType` is the only type renderer, with declaration, long, compact and
runtime modes. Named array axes now render as `arrayed[frame]<colmap-cams>`;
unnamed arrays remain `arrayed<T>`. Recursive structures retain every layer,
including unnamed ones. The old `arrayed<frame> of T` ambiguity is removed.
Compact/runtime counts retain `T(pose:21 intr:1)[frame:100]` and inner-first
brackets. Long inspection labels retain available measured count details.
Unmeasured legacy numbers are no longer displayed by the canvas.

`formatTypeLabel` / `formatTypeLabelLong` remain as thin compatibility facades
used by edge chips, inspectors and snapshot labels; they contain no formatting
rules. Frontend rejection messages call the same facade. Phase 3 subsequently
migrated backend rejection messages to the Python renderer. There is still no
visible rejection popup: the existing console warning is tested without adding a new interaction.

Snapshot edge labels continue to use frozen producer `resolved_outputs` and
snapshot job handles. Card layout and snapshot interaction are unchanged in this
phase. Pack metadata still comes from the catalog for the snapshot's algorithm
version, as before; there is no new claim that historical input declarations are
separately frozen. Snapshot node cards now forward the persisted `arrayed_toggle` to their
visual wrapper and port tooltip context. The package's `arrayable` capability
continues to come from the catalog. React Flow observes the wrapper size; snapshot data refreshes also explicitly
request handle remeasurement after commit (same-size refreshes may not trigger
ResizeObserver); browser tests switch saved modes with the same node IDs and
check edge endpoints at 50%, 100% and 150% zoom.

Regression distinction: a chip without runtime data can show the **known contract**
with an unknown count (`image[frame:?]`), while the tooltip's actual-data row must
remain `未知`. This has a dedicated test; summary absence must not erase known
contract information or invent an actual result.

At phase 2 acceptance, backend compatibility and measurement evidence were
intentionally deferred. Phases 3–4 above now implement them. Legacy values still
do not prove complete coverage, and arbitrary filesystem paths or formatted
strings are never used to infer declaration semantics.


## Old-data display policy (post phase 5)

The API compatibility contract and canvas fallback policy are deliberately separate:

| Category | Path / condition | Policy |
|---|---|---|
| A: external API | Graph `resolved_outputs`, handle `tags`, `dim_labels`, `dim_sizes`, `element_count`, `internal_count_items`, etc. | Keep existing fields and meanings. No backend schema or serialization changes in this cleanup. |
| A/current draft | `typeFromLegacy`, `declarationType`, `effectiveOutputType` on structured catalog/resolved fields | Keep. Unsaved drafts still need these adapters; they are not historical measurement reconstruction. |
| B: old summary | Summary/cache numeric fields, directory entries, first-child entry counts without `runtime_shape` | Do not reconstruct measurements. Inline and fetched facts share `factsFromSummary`. |
| B: missing evidence | `legacyValue` and unknown new axes supplemented with old dimensions | Ignore, even when an old number exists. Actual-data row is unknown. |
| Normal validation | Missing, malformed, unsupported-version or wrong-handle `runtime_shape` | Safely treat as unmeasured. This guard is retained to avoid render failures. |
| Normal unavailable metadata | Missing catalog pack / optional preview / no current job | Keep safe defaults; these can occur for new snapshots and offline nodes too. |

Known declarations remain useful without observations: a compact chip may show
`image[frame:?]`; its tooltip actual-data row stays `未知`. This does not copy a
contract into a measured result. Old snapshots still render and edges remain
selectable; tests include old numeric fields deliberately carrying 999/888 so
accidental resurrection is detectable. Snapshot metadata refreshes preserve React
Flow's measured node sizes, so a missing historical preview does not discard
edge endpoint measurements while the card DOM remains unchanged.

New observations continue to distinguish `sampled` and `complete`; old data need
not recover either status. The snapshot `arrayed_toggle` omission affected new snapshots too and was fixed
separately by forwarding the saved field, without changing persistence or execution.

Snapshot node field audit: `arrayed_toggle` is persisted on `GraphNode` and is
forwarded explicitly; `arrayable` comes with the matched catalog pack. Node ID,
position, algorithm identity, params and assigned device are already forwarded.
`preview_open` seeds local preview state. `parallelism` and `batch_size` are
execution settings not read by `AlgorithmNode`; they need no visual prop.
`resolved_outputs` / `port_types` remain on the graph for type resolution, not
card layout. Runtime/preview data is derived from snapshot jobs/handles; draft
staleness and live partial-shard previews are not persisted node fields.

### Input axis-name policy

`declaration.require_axis_match` defaults to `true`. The input contract may set
it to `false` to skip only axis-label comparison, recursively. Collection depth
and leaf tags retain their existing rules, including explicit generic `any`.
The catalog carries the same input field; validation and drag feedback read it.
Resolved types keep their original labels: this policy does not rename or reshape
values, change observations, or affect fan-out scheduling. Old consumers keep the
existing fields; missing policy means the original strict comparison.
