# flatten-array 0.1.0

Generic fixed-rank array reshape:

```text
arrayed[outer,inner]<T> -> arrayed<T>
```

Each `<outer>/<inner>` leaf is exposed at the output root as
`<outer>__<inner>`. Leaves are opaque files or directories and are symlinked,
so the operation does not inspect, decode, copy, or rewrite T. Output tags are
resolved from the connected input through `tags_from: arr`.

The separator is reserved in both source component names. A hidden
`.flatten-array/manifest.json` records the reversible mapping. HoloLab's type
system currently carries axis count in `dim_labels`, so arbitrary input rank is
not expressible; this node intentionally supports exactly two axes.
