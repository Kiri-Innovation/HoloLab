# Native FreeTimeGS PLY viewer

`ftgsply` is the data tag and preview viewer ID for native `.ftgs.ply`.
The global tag registry resolves any producer's `ftgsply` output to this
viewer. Explicit manifest preview declarations remain authoritative.
`freetimegs-model` continues to describe training/checkpoint state, not PLY.

The frontend uses `embed=ftgsply` and `ftgsply_scene`. It shares the existing
Gaussian iframe, camera/focus handling and time controls with SplaTV. Playback
preferences use `colmaputil.ftgsply.playback`; the SplaTV key remains unchanged.
The time range is normalized [0, 1]. The playback cycle is a UI preference,
not the recording's frame rate or duration.

The vendored ColmapUtil pins HoloEngineRuntime `17c9101` (FTGS implementation
`b7f56e7`). That runtime detects FTGS from PLY contents and loads it through
`usePlyLoader` into the existing 4D pipeline. No new parser, shader, converter,
or worker is introduced here. The URL pathname must retain `.ftgs.ply`.

Direct file outputs need only the `ftgsply` tag. A directory output must
specify its exact member, for example:

```yaml
outputs:
  ply:
    tags: [ftgsply]
    storage: dir
    preview:
      viewer: ftgsply
      member: model.ftgs.ply
```

FreeTimeGS to-viewer 0.1.1 uses that directory contract because its existing
exporter writes `model.ftgs.ply` inside the output directory. Version 0.1.0
incorrectly declared file storage. Keep the original source root/version;
manifest bytes are part of identity. Do not relabel historical handles or
saved snapshots. New versions require the normal install/run workflow after
these local changes are integrated; no registry publication is part of this
change.

Validation includes read-only ASGI tests with in-memory stores (no database
writes), preview dispatch tests, and the existing native FTGS/legacy runtime
tests. The preview dispatch tests stub iframe contents deliberately; they
verify routing, not graphics. Actual browser rendering needs WebGL2 and a
matching reference camera for visual acceptance.
