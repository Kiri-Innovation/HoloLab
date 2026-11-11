# Camera array preview

The existing `colmap-single-cam` tag describes a JSON file with exactly
`model`, `width`, `height`, `params`, `qvec`, and `tvec`. The quaternion is
world-to-camera, ordered `(w, x, y, z)`. An array is a directory of these
files, including extensionless fanout members. It remains a structural array
of this tag; no array-specific data type or conversion pack is introduced.

The array-only preview rule selects `viewer.html`, using the existing
type-owned HTML entry mechanism. There is no server-side preview preparation
hook. The wrapper fetches members with four concurrent requests and assembles
standard `cameras.bin`, `images.bin`, and an empty `points3D.bin`. Each member
gets distinct camera/image IDs, even if poses coincide. Geometry is preserved;
there are no synthetic timestamps, keypoints, image pixels, or 3D points.

The preview files use a temporary browser OPFS directory when available,
falling back to in-memory Blobs on browsers without OPFS (including insecure
HTTP contexts). Refresh/replacement and normal page exit remove the directory;
abnormal browser termination can leave browser-local temporary data until its
storage is cleared. Job outputs are only read. Nothing is written to the node.

ColmapUtil receives the files through its existing `colmap-load-files` protocol.
The declaration uses `cams_only: true`; the wrapper supplies only camera geometry
and frames the initial view around all camera centers. No frontend component
build or service restart is needed: the registry watches this declaration and
serves its assets with `Cache-Control: no-store`.
