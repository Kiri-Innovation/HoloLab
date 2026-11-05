# Type-owned array views

Status: staged implementation. Contract only at stage 1; existing services do
not automatically execute these declarations until the planner/executor rollout.

A manifest may own `type_capabilities: {<tag>: {array_views: {<view>: ...}}}`.
Each view declares version 1, a one-dimensional `array` (tags, arrayed: true,
dim_labels, storage: dir), a `partition` (policy: source_exposure_groups,
order: timestamp_then_stable_key, timestamp_unit: ns), and exactly two hooks:
`to_array` and `from_array`. A hook identifies a scoped pack name, exact version,
input and output port. There are no per-operation implementations.

All capability objects reject unknown fields. Hooks misplaced on ordinary ports
or at manifest root are errors rather than ignored extension fields. Repeated
advertisements of the same owner must agree; competing owners of the same type
and view fail resolution, independent of enumeration order.

Declarations do not mutate scalar handles or weaken ObjectType/ArrayType
compatibility. Implementations must materialize real arrays of independently
valid elements, preserve source identity, and refuse restoration when provenance
or entity contents conflict. Array positions are not entity IDs or timestamps.
No frontend or live-service rollout is included in the contract stage.
