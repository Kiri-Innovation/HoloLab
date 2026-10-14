# Gray-wall person masks

Connect `merge-colmap.folder → person-gray-mask.colmap`, then connect
`person-gray-mask.masks → fastgs-pano-train.masks`. Keep the original
`merge-colmap.folder → fastgs-pano-train.colmap` edge.

The CPU-only node uses NumPy and OpenCV already installed in the mapped
`fastgs` environment. It clusters neutral border colors into five Lab wall tones (including shadows), builds
adaptive foreground/background seeds, refines with GrabCut at up to 640 px,
keeps the largest connected subject, and closes tiny pinholes. No weights,
downloads, alpha images, or modifications to source RGB/COLMAP are involved.

Each image gets a binary single-channel PNG with the same stem and dimensions.
Duplicate stems, unreadable images, missing masks, non-binary masks, insufficient
wall samples and implausible coverage fail the job. Progress is saved after
every frame; `.done` is created only after complete validation. The report
contains coverage/color statistics and first/middle/last RGB-mask-composite
triptychs.

This is a controlled gray-wall extractor, not a general human segmentation
model. Neutral gray clothing, background objects, multiple subjects and fine
hair can be problematic. Review report images before training. Ignoring RGB
loss outside a silhouette does not itself guarantee zero opacity there:
existing sparse points and Gaussian tails may remain. Check rendered alpha
on a light background, not only RGB on black.

Run tests with the mapped fastgs Python:

```sh
python test_mask.py
```
