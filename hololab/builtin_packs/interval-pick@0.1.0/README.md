# Interval Pick 0.1.0

`in: arrayed<T>[axis, ...]` → `out: arrayed<T>[axis, ...]`.
Select outer indexes `start, start + interval, ...`; defaults are start=0,
interval=2. The unit is **index**, never time. Names are sorted by basename
bytes, hidden entries are excluded, and names are not interpreted as numbers.
The output length is `max(0, ceil((N-start)/interval))`.

Like builtin/slice, the output is a real directory containing symlinks to
resolved source elements, preserving names, content, and inner arrays. Sources
must remain available. Unlike slice 0.1.0, empty input/selection produces a valid
empty output. Output must be empty and disjoint from input; stale output content
is rejected rather than silently retained.

Uses `tags: [any]`, `tags_from: in`, `dim_labels_from_input: in`, and
`dim_labels_drop_outer: 0`. Element types and all axis names flow through;
only the outer length changes. Does not require or implement array views.

Standard-library Python only, host runtime, no network or third-party packages.
`interval` has manifest `min: 1`; `start` has `min: 0`. Preparation rejects
invalid values before creating a job. The script also validates direct CLI use.
