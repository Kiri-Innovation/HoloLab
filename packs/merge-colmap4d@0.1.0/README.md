# merge-colmap4d incubating pack

This Kiri-specific HoloLab node assembles materialized temporal groups and their
per-group COLMAP reconstructions into one scalar colmap4d model. It intentionally
stays in HoloLab: temporal grouping materialization and the STG pipeline are not
portable format-level integrations.

The generic colmap4d validation, conversion, inspection, and viewing nodes remain
candidates for migration to `colmap4d/integrations/hololab/` under the migration
conditions documented by the temporal-grouping incubation pack.
