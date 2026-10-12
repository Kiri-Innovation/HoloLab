# Port type IR: phased unification

Status: phases 1–2 implemented. Frontend outlets now use explicit legacy adapters
and a terminal IR renderer. Backend wire payloads, validation, runtime execution
and pack contracts are unchanged.
Source: `hololab/frontend/src/types/portTypeIR.ts`.

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

Phase 2 provides explicit source-aware adapters (below); phase 3 will centralize
the normalization. Reuse `effective_port_arrayed`, `effective_output_tags` and
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
If the old API supplies a number but no coverage guarantee, keep it as
`legacyValue`, with unknown evidence; do not discard it or relabel it complete.
Zero is a real measured value. Unknown shape differs from measured scalar `[]`.

`RuntimeShape.axes` is outer-first. Each axis records its role
(`collection` / `content` / `unknown`) and size evidence. Overall axis-list
coverage and individual size coverage may differ. Internal counts have independent
evidence; e.g. a complete outer directory count and a sampled pose count coexist.
No measurement is automatically replicated to every element when a type is
wrapped or broadcast. Full runtime derivation/projection is deferred to phase 4.

This phase supplies compile-time TS types and JSON round-trip tests, not a runtime
JSON validator. External payloads must be validated before becoming this IR.

## Backend / API design (not implemented yet)

In phase 3 add Python equivalents of the versioned semantic envelope using the
existing Pydantic infrastructure, rather than modifying the manifest schema.
The normalizer consumes existing `InputSpec`/`OutputSpec`, graph context, effective
resolvers and node execution mode; it returns both port directions and both views.
Backend compatibility and response generation should consume that same result.
Frontend pre-autosave fallback must follow the same vectors/rules.

Proposed additive payload under each graph node:

```ts
type NodePortTypes = {
  schemaVersion: 1;
  inputs: Record<string, PortTypeIR>;
  outputs: Record<string, PortTypeIR>;
};
// graphNode.port_types?: NodePortTypes
```

Values are structured JSON objects, never type strings. Graph GET and `/api/resolve` `current_draft` should
use the same producer. Keep `resolved_outputs` and existing summary fields during
migration, deriving them from the new normalized result where representable.
Do not send historical runtime observations as proof of current draft validity.
Phase 4 adds structured evidence to summary responses; phase 3 can explicitly
report unknown observation coverage alongside legacy values.

No Python module or endpoint change is needed for this isolated first phase.
An unconsumed Python duplicate now would create another model to drift. The
wire contract, schema validation, serialization tests and legacy fallbacks must
ship together in phase 3. Cross-client consumers (including Flops) will need the
versioned additive schema and should continue using legacy fields until migrated.

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

Phase 1 was accepted before phase 2 began. Stop after phase 2 for review;
subsequent stages require separate acceptance:

2. Implemented: source-aware adapters and one renderer across five outlets; preserve captions
   without deriving semantics from them. Test labeled/unlabeled and already-
   aggregate sources, which existing screenshot fixtures do not exhaust.
3. Backend normalization shared by resolved types and validation, with additive
   API/schema; reconcile current frontend/backend dimension/alias differences.
4. Runtime observation provenance and coverage; avoid first-element samples being
   treated as globally complete or historical observations as current guarantees.
5. Update pack specification and dimension semantics. In particular the old
   statement “compatibility only checks tags” is incomplete. Retain current YAML
   and pack compatibility; report any schema change before implementing it.

No pack files, scheduler behavior or runtime processes are changed in phase 1.


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
- `runtimeFromLegacy` maps summary facts to observation fields with unknown
  coverage and preserved `legacyValue`; it does not promote a count to complete
  evidence. `elementRuntime` preserves the existing third-row element projection
  and retains count provenance. This is not a per-shard verification API.

`renderPortType` is the only type renderer, with declaration, long, compact and
runtime modes. Named array axes now render as `arrayed[frame]<colmap-cams>`;
unnamed arrays remain `arrayed<T>`. Recursive structures retain every layer,
including unnamed ones. The old `arrayed<frame> of T` ambiguity is removed.
Compact/runtime counts retain `T(pose:21 intr:1)[frame:100]` and inner-first
brackets. Long inspection labels retain available count details. Legacy values
remain visible during migration but the IR never labels their coverage complete.

`formatTypeLabel` / `formatTypeLabelLong` remain as thin compatibility facades
used by edge chips, inspectors and snapshot labels; they contain no formatting
rules. Frontend rejection messages call the same facade. The backend rejection
message remains unchanged until phase 3. There is still no visible rejection
popup: the existing console warning is tested without adding a new interaction.

Snapshot edge labels continue to use frozen producer `resolved_outputs` and
snapshot job handles. Card layout and snapshot interaction are unchanged in this
phase. Pack metadata still comes from the catalog for the snapshot's algorithm
version, as before; there is no new claim that historical input declarations are
separately frozen. Existing snapshot node cards do not forward `arrayed_toggle`
to their visual wrapper: that pre-existing layout/mode limitation is not changed
here, because enabling it would move connection anchors. The historical wire
type is still preserved and explicitly tested independently of the card layout.

Regression distinction: a chip without runtime data can show the **known contract**
with an unknown count (`image[frame:?]`), while the tooltip's actual-data row must
remain `未知`. This has a dedicated test; summary absence must not erase known
contract information or invent an actual result.

Remaining boundaries are intentional: phase 2 does not change frontend/backend
compatibility policy, discover array provenance from arbitrary filesystem paths,
or claim that legacy summaries record full measurement coverage. Those concerns
remain phases 3–4. Current resolver behavior is reused, not duplicated in adapters.
