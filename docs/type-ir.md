---
title: Port type system
group: System design
order: 30
---
# Port type IR

HoloLab represents port types as structured data. Display labels are a terminal
rendering of that data; do not parse labels to decide compatibility, array depth
or scheduling. Manifests continue to use YAML fields rather than a new type-string
syntax.

## Versioned envelope

`PortTypeIR` has `schemaVersion: 1` and three separate records:

| Record | Meaning |
| --- | --- |
| `declaration` | Original manifest fields and derivation directives. |
| `views` | Normalized invocation and aggregate types, with participation mode. |
| `runtime` | Observations for an actual handle, including evidence and scope. |

An algorithm's declaration is not interchangeable with its effective aggregate
type. Runtime counts do not alter declarations or establish compatibility.

## Recursive type representation

```ts
type TypeExpr =
  | { kind: 'unknown'; reason: string }
  | { kind: 'object'; tags: string[]; contentAxes: { label: string | null }[] }
  | { kind: 'array'; axis: { label: string | null }; element: TypeExpr };
```

Each `array` adds one collection layer. Axes are outer-first. An object's
`contentAxes` do not create shard-enumeration layers. An unnamed axis uses
`null`; neither label spelling nor a numeric count supplies a missing meaning.
An array with an unknown element type is still a known array.

`any` is an explicit generic tag. It differs from `unknown`, which represents
information that could not be resolved. Removed image tags are invalid even when
combined with `any`.

## Invocation, aggregate and broadcast

Participation is `none`, `map-input`, `broadcast-input` or `collect-output`.
The invocation view describes one algorithm call. The aggregate view describes
the value on the wire.

With node fan-out, a mapped input gains one outer collection around its declared
invocation value. A call that consumes a `[cam]` image collection therefore reads
one element from an aggregate `[frame, cam]` collection. Each collected output
also gains one outer layer. The current executor's enumeration depth is one.

A broadcast input passes its entire declared value to each shard. It does not
remove layers, create N physical copies, or make a collection satisfy a single
image declaration. Generic array helpers can express several layers, but that
does not mean the executor dispatches through all of them in one fan-out step.

`image` denotes one fully decodable single-frame file. Image collections declare
`arrayed`, directory storage and outer-first axis labels explicitly. Images have
no implicit image-count content axis.

## Compatibility

Validation uses structured effective types. Ordinary object leaves compare tags;
ordinary collection values compare their layers recursively. Explicit `any`
follows the generic compatibility rule. Unresolved types are not evidence of a
valid connection.

Input `require_axis_label_match` defaults to `true`. At each compared collection
layer, two non-empty labels must agree unless the input explicitly disables
axis-label matching. Disabling it affects labels only: it does not rename data,
reshape collections, alter observations or change scheduling.

`storage` controls file transport and is not an independent edge-matching rule.
Strict image declarations and delivered data must still satisfy their storage
and shape contract. A wire that passes type checks is not proof that the algorithm
will accept every aspect of the data's business meaning.

## Runtime observations

Each observed field carries evidence:

```ts
type Evidence<T> =
  | { status: 'unknown'; reason: string }
  | { status: 'sampled'; value: T; source: string; sample: string }
  | { status: 'complete'; value: T; source: string };
```

`complete` applies to the identified handle and measured field, not every object
in a workflow. A complete outer collection count can coexist with a sampled
internal pose count. Zero is a measurement; unknown is not zero. An unknown shape
is also distinct from a measured scalar shape.

`RuntimeShape` identifies its handle and carries tag observations, an outer-first
axis list, and internal counts. Axes have `collection`, `content` or `unknown`
roles, each with size evidence. Overall axis-list coverage and per-axis size
coverage can differ.

Image contract validation checks every image leaf. Sampled summaries of opaque
bundles are observations, not exhaustive validation of bundle contents.

## API consumption

Graph-node `port_types` contains versioned `inputs` and `outputs`. It is derived
from the Pack catalog; clients should handle it being unavailable. It may be
accepted in a graph round trip but is not persisted as a user-authored contract.
Draft contracts do not acquire historical measurements merely because a previous
run exists. Read the associated output handles or handle summary for observations.

Existing projections such as `resolved_outputs` remain available. Unsaved
frontend drafts can use structured catalog adapters without waiting for a saved
server response. Do not wrap already-normalized aggregate data a second time.

For runtime data, validate the version, structure and handle identity. Missing,
malformed or mismatched observations remain unmeasured. Legacy numeric fields
and `legacyValue` are not reconstructed into current canvas measurement evidence.
Known declarations can still be displayed while actual-data fields remain unknown.

## Implementation entry points

- Backend models and compatibility: `hololab/gateway/port_type_ir.py`.
- Workflow normalization: `hololab/gateway/workflows.py`.
- Observation construction: `hololab/gateway/type_observation.py`.
- Frontend structured types and operations: `hololab/frontend/src/types/portTypeIR.ts`.

See [Pack specification](pack-spec.md) for manifest fields and
[Workflow schema](workflow-schema.md) for graph records.

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
These operations transform structured types; compatibility policy remains a separate concern.

## API shapes and observation scope

```ts
type NodePortTypes = {
  schemaVersion: 1;
  inputs: Record<string, PortTypeIR>;
  outputs: Record<string, PortTypeIR>;
};
// graphNode.port_types and /api/resolve current_draft.port_types
// Handle summaries and latest_run.output_handles[port]:
// runtime_shape?: RuntimeShape & { schemaVersion: 1 }
```

`resolved_outputs` retains `{tags, arrayed, dim_labels}` for each output.
`workflows.resolved_port_types` supplies normalized types to validation and API
serialization. The whole-workflow run route uses the same catalog conversion.
Derived output dimensions therefore participate in compatibility; a derived
collection is not silently treated as a scalar.

`type_observation.observe_summary` constructs observations at the probe boundary,
before caching. `InternalCountItem {label: string, value: number}` preserves the
legacy `internal_count_items` shape; structured `internalCounts` carry evidence.

- Registered tags have complete metadata scope, not proof of file contents.
- Uniform directory dimensions can have complete directory-tree coverage.
- Counts read at a representative child are sampled; counts at the complete
  handle-root scope can be complete. Sample paths are relative to the handle.
- Missing or ragged shape remains unknown. Old numbers are not upgraded to
  measured evidence.

Summary cache keys include `(handle_id, dim_labels)`; tombstoning invalidates the
handle's cached scopes. `runtimeObservation.ts` validates runtime envelopes;
`runtimeFromSummary` rejects evidence for the wrong handle. Element projections
retain evidence scope and do not fabricate unmeasured dimensions.

## Frontend adapters and rendering

`legacyPortType.ts` is the boundary for structured legacy fields:

- `typeFromLegacy` accepts effective edge fields, including resolved outputs.
- `declarationType` reads the original port declaration without reimplementing
  graph inference.
- `legacyViews` requires explicit invocation or aggregate provenance. Aggregate
  fields already contain effective fan-out layers and must not be wrapped again.
- `adaptPortType` consumes effective aggregate fields. The resolver, rather than
  this adapter, determines the additional layer for node fan-out.
- `runtimeFromSummary` uses validated evidence only; `elementRuntime` projects
  observation structure without changing its coverage.

`renderPortType` supports declaration, long, compact and runtime modes.
`formatTypeLabel` and `formatTypeLabelLong` are compatibility facades. Named
arrays render as `arrayed[frame]<T>`; labels must never be parsed back into types.
A known contract can be displayed with an unknown count while the actual-data
row remains unknown.

| Display surface | Type and observation context |
| --- | --- |
| Port and edge tooltips | Declaration, normalized views, participation and evidence. |
| Compact edge chip | Effective wire type and available measurements. |
| Edge inspector | Full effective tags and axes, plus available observations. |
| Snapshot labels | Frozen producer resolved outputs and snapshot job handles. |
| Connection feedback | Structured source/target types and the compatibility reason. |

Snapshot Pack declarations still come from the catalog for the recorded algorithm
version; do not assume every historical input declaration is frozen separately.
The saved `arrayed_toggle` is forwarded to card and tooltip context, while the
Pack's `arrayable` capability comes from its catalog entry. `parallelism` and
`batch_size` are execution settings, not visual array-depth rules. Preview data
comes from snapshot jobs and handles, not current draft staleness.

## Legacy data policy

| Data | Policy |
| --- | --- |
| Existing API tags, dimensions, counts and resolved outputs | Keep their compatible wire projections. |
| Structured current-draft fields | Continue using explicit adapters for unsaved edits. |
| Old numeric summaries without runtime evidence | Do not reconstruct current canvas measurements. |
| `legacyValue` or missing new-axis evidence | Treat as unmeasured, even if an old number exists. |
| Missing, malformed, unsupported-version or wrong-handle envelope | Fall back to unknown observations. |
| Unavailable Pack or preview metadata | Preserve safe display defaults without inventing facts. |
