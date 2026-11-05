# col4d/colmap4d-to-array@0.1.0

Declares the type-owned `colmap4d/exposure_groups` view. Produces a real
one-dimensional `arrayed<colmap4d>[frame]`, ordered by original int64 ns. Equal
exposure times form one group; positions are zero based, not IMAGE_IDs.

Initial domain: canonical BIN, strictly increasing unique point IDs, complete
ordered points_t, one source observation per point, exact reciprocal tracks,
complete image times and valid pixels. Multi-track and noncanonical layouts fail
explicitly. Source files are not changed. Each group contains a complete model,
original point/image IDs, observations, ns, metadata and independently copied
pixels. Cameras are retained in full, including shared/unused camera records.
No source-directory symlinks are emitted.

Per-element `.array-view.json` carries source namespace (SHA-256 of source file
hashes), position, exposure ns, image IDs, provider version and file hashes.
The root manifest records the ordered partition. Ordinary slice/get-index can
operate on these actual directories without understanding their internal format.

`col4d/colmap4d-from-array@0.1.0` restores unmodified selections. This first
version deliberately rejects edited elements instead of inventing reconciliation
for altered observations. Duplicate selections deduplicate identical entities;
equal timestamps do not deduplicate distinct IDs. Clock/metadata/namespace or
content conflicts fail; IDs are never remapped. Empty selection restoration is
currently an explicit error. Intermediate arrays remain inspectable artifacts.

Validation: `python -m unittest discover -s <this directory> -p test_convert.py`.
Requires the declared colmap4d, numpy and pillow environment. Large models use
array/mmap records; restore currently holds concatenated/sorted point arrays in
memory and needs substantially more than the file size. This is not a streaming
low-memory merger. Converter copies in both providers must remain byte-identical.
