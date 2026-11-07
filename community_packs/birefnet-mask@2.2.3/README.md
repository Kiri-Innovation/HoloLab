# BiRefNet mask 2.2.3

Derived from packages/BiRefNet 2.1.0: video and ordered image arrays, frame selection,
soft alpha and strict local state-dict loading are retained. The frame extractor
and positional naming helper are bundled from HoloLab (MIT), avoiding an undeclared
HoloLab installation inside the inference environment. BiRefNet vendor license: LICENSE.

Run `hololab pack install community/birefnet-mask@2.2.3`, then
`hololab pack setup community/birefnet-mask@2.2.3`.
Setup creates/maps the dedicated `birefnet-mask` conda environment. A800's existing
`birefnet -> kiri` production mapping is intentionally untouched.

Weights: ZhengPeng7/BiRefNet at e2bf8e4460fc8fa32bba5ea4d94b3233d367b0e4.
The pinned model.safetensors SHA-256 is
9ab37426bf4de0567af6b5d21b16151357149139362e6e8992021b8ce356a154
(444473596 bytes), matching the original A800 model. Vendored Python model code
is used; setup needs only the safetensors file. Default cache is the sibling
`birefnet/` of the framework's HF_HOME, under node/weights. Override with
BIREFNET_WEIGHTS_DIR for both setup and jobs. An explicit checkpoint overrides it.
Cached files are hashed before reuse; downloads are verified before atomic rename.
Inference performs no network access. HF_ENDPOINT/HTTPS_PROXY configure downloads.

Version 2.2.3 enables framework fan-out over camera videos (or batches of image
arrays). Enable the node arrayed toggle for an outer camera array; leave it off
for one video or one frame array. Each invocation still produces a frame array.
