# flatten-image-groups 0.1.0

This is an explicit structural adapter from `arrayed[group,cam]<image>` to a
physical one-dimensional `arrayed<image>`.

Each `<group>/<filename>` leaf becomes the root-level file
`<group>__<filename>`. The separator is reserved and rejected in either source
component, making the transform reversible. Files are hardlinked when possible
and copied otherwise; image bytes and XMP are not rewritten.

The tradeoff is intentional: group membership is no longer part of the output
type. It survives as a strict filename convention and as an audit-only hidden
manifest. Consumers that need groups must validate and decode that convention.
