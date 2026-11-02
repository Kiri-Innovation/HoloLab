# mask-to-alpha 0.1.0

One invocation consumes two flat image arrays and writes RGBA PNGs named
`p_<index:016d>.png`. Each array is enumerated independently in basename byte
order. Pairing is by position, never matching source filenames. Enabling the
node's arrayed toggle adds an outer axis (e.g. cameras around frames).

RGB is preserved after decoding to RGB; existing alpha is replaced. Masks are
converted to Pillow `L` grayscale, preserving 8-bit grayscale values exactly.
No resize, threshold, multiplication with existing alpha, or premultiplication
is applied. Empty arrays, unequal counts, and unequal dimensions fail.

`verify_source_hash=true` is the default: require the mask group's
`.validation.json`, read its ordered `frames` records, and compare each
`rgb_sha256` with SHA256 of the decoded source's RGB bytes. Missing/invalid
metadata and mismatches fail. This is the format emitted by
`community/birefnet-mask@0.1.1`. For other masks without provenance, verification
can be explicitly disabled; the output report records that choice.

The output `.validation.json` records positional RGB hashes and whether they
were verified. `.hololab-done` is written only after all pairs succeed.
Partial failed output has no completion marker; retry into a fresh directory.

Runtime: Python, NumPy, Pillow; the configured `cam3` environment. Processing is
sequential within each invocation to bound memory; recommended outer fan-out
is four camera shards, one camera per shard.
