# pi4-output-beautified

A directory handle containing the existing `pi4_web_scene_v1` export. The
`scene.json` manifest describes the gzip-compressed binary and the dtype,
shape, byte offset, and length of its arrays. The package may carry an
optional sibling MP4, referenced by `video`. The compact viewer does not
request it.

The type entry receives the selected handle's proxy URL through HoloLab's
viewer state message. It loads the manifest and compressed binary from that
directory through the existing gateway proxy. The compact layout adapts the
standalone preview's HTML/CSS. The loader and renderer use browser APIs and
WebGL, with no external modules.
