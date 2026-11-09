# col4d/colmap4d-trim@0.2.0

Trim `colmap4d`, `colmap4d-cams`, `colmap4d-images` or
`colmap4d-point-cloud` to the absolute nanosecond window
`[start_time_ns, end_time_ns)`. Defaults: start `0`, end `-1` (unbounded).
Times, IDs and `clock_domain` keep their original values. Empty windows fail;
out-of-range windows warn and intersect available records.

## Fan-out

Version 0.2.0 adds `arrayable: true`; the per-element trimming code is identical
to 0.1.0, which remains available.

Enable fan-out for an actual outer array of supported components. For example,
`arrayed<colmap4d-cams>[camera]` from `repeat-into-4d` maps one complete camera
component to each shard. Every shard receives the same absolute time window.
The scheduler maps exactly one outer layer; it does not split internal images,
frames, cameras, points or the `sparse/0` directory. A time-group array similarly
maps one complete group per shard; a group outside the window fails the existing
empty-window rule rather than being silently dropped.

With fan-out enabled, outputs collect into an array with the mapped outer axis.
`parallelism: 2` permits up to two concurrent shards. With fan-out disabled,
input and output are scalar components, with the same behavior as 0.1.0.
An array wired while fan-out is disabled is a cardinality mismatch.

A scalar component has no outer array layer. This also applies to a complete
scalar `colmap4d` model, regardless of how many cameras or frames it contains.
Keep fan-out off for scalar inputs. The existing workflow type validator rejects
a scalar wire to this node with fan-out on (`arrayed cardinality mismatch`);
there is no implicit single-element wrapping or model-directory enumeration.
To explicitly run one shard, first assemble a one-element array using the
platform's array assembly. An actual array of complete models can be mapped,
with each model kept intact inside its shard.

The useful camera-array interpretation is that every camera sees the same
absolute time window. This is the expected use with `repeat-into-4d`; the node
does not infer camera arrays from a scalar model.

## Component requirements

Timed pixels require `times` plus image records or `image_names.json`. Legacy
pixel-only components without time identity fail explicitly. Whole models trim
all three components and prune reciprocal observations/tracks without compacting
observation indices. If pruning invalidates a `track_centroid` time claim, the
operation fails rather than changing timestamps or provenance.

Readers/writers are provided by `colmap4d`. Dense models load full observations
and tracks into CPU RAM; concurrent shards multiply that memory demand. Choose
parallelism for the size of each component. No GPU is required.
