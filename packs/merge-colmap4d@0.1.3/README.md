# merge-colmap4d 0.1.3

Same assembly behavior as 0.1.2, with the flat image input now produced by the
generic `flatten-array@0.1.0` operation rather than an image-specific adapter.

`flatten-array.flat` declares `tags: [any]` plus `tags_from: arr`; when its
input is `image`, HoloLab resolves the output to `arrayed<image>`. This pack
continues to require that concrete type and strictly parses the generic
`<outer>__<inner>` names as `gXXXXXX__camN.jpg` for reconstruction alignment.
