# colmap4d temporal grouping 0.1.1

This Kiri-owned incubating HoloLab pack materializes fixed-grid temporal groups
with `colmap4d.groups.group_by_grid`. Version 0.1.1 adds the `exposure_timeline`
output expected by the existing `rig_timeline` viewer. Its directory contains
`exposure_timeline_full.{png,html}`, `exposure_timeline_zoom.{png,html}`, and
`report.md`, matching the established storage contract.

The images and the visualization are produced from one exposure-midpoint record
set (`wallClockEpochNs + exposureNs // 2`) and one colmap4d grouping result.
The original `rig-temporal-grouping@0.1.5` pack remains unchanged.

This node is Kiri-specific materialization policy and will not move into the
public colmap4d repository. Generic validate/convert/inspect/view integrations
may move to `colmap4d/integrations/hololab/` after HoloLab's schema, validator,
installation instructions, CI contract, and compatibility policy are public.

The directory is self-contained: it has no absolute paths or repository-relative
imports, calls only public colmap4d APIs, requires `colmap4d>=1.0.2`, and keeps
all viewer-support code inside the pack directory.
