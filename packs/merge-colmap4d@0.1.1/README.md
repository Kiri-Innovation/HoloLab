# merge-colmap4d 0.1.1

Assembles one scalar colmap4d model directly from the arrayed `points`,
`images`, and `cams` outputs of the triangulation/undistortion branch.

Unlike 0.1.0, this version does not consume an intermediate `colmap-folder`
or the temporal-grouping manifest. Every image time is read from the image's
preserved HoloLab XMP as `wallClockEpochNs + exposureNs // 2`. Missing fields,
mixed clock domains, or an XMP `alias` that disagrees with the image filename
are hard errors.

The group axis remains an assembly concern only. IDs are remapped globally,
the output is one ordinary COLMAP sparse model plus colmap4d sidecars, and
the seven physical camera identities are shared across all groups.

As in `merge-colmap@0.4.0`, image observation rows are empty. Triangulation
tracks index the triangulator database keypoint table, while `cams/images.txt`
contains an older SfM-prior observation table; combining the two would attach
points to incorrect pixels. Poses, tracks, image times, and point times remain
available, matching the 0.1.0 assembly contract.
