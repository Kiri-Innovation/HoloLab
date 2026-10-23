# Image crop viewer verification

Implemented an expanded-node editor for `kiri/image-crop@0.1.0`.

## Files

- `src/canvas/AlgorithmNode.tsx`: parallel image-crop branch beside ComposeVideoEditor; preserves previous output in a details panel.
- `src/canvas/ImageCropEditor.tsx`: connected input lookup, stored-pixel canvas, rectangle drag, eight resize handles, live padding values, numeric fallback, read-only guards and cancellation.
- `src/canvas/imageCropEditor.css`: existing canvas design tokens for light and dark themes.
- `src/canvas/imageCropGeometry.ts` and `.test.ts`: top-left stored-pixel coordinates, signed paddings, half-away-from-zero rounding, original-pixel retention and backend output limits.
- `src/canvas/imageCropPixels.ts` and `.test.ts`: remove EXIF from a browser-only display copy, without modifying source bytes or image samples. Chromium otherwise rotates even with `imageOrientation: none`.
- `e2e/image-crop.spec.ts`, `e2e/fixtures/image-crop.html`, `e2e/fixtures/image-crop.tsx`: read-only, missing image, failed image decode, Escape cancellation and JPEG/PNG/WebP orientation coverage.
- `e2e/image-crop-live.mjs`: reproducible real UI / real job acceptance test.

## Compose pattern

Like ComposeVideoEditor, the editor reads React Flow nodes and incoming edges, resolves the current producer handle with `getHandleSummary`, blocks canvas drag/pan, uses pointer capture and a local gesture draft, and disables writes in read-only mode. The four crop values are manifest **params**, not input ports, so commits use `onParamsChange(id, patch)` directly. Inspector and viewer read the same node params. Drag release submits all four pads together; numeric edits submit their changed key. The existing drift and execution mechanisms remain responsible for staleness and explicit runs.

Geometry: `(x0,y0)=(left,top)`; `(x1,y1)=(W-right,H-bottom)`. Values are rounded exactly like crop.py. Negative values extend beyond the original image. Drag scaling uses the stage's actual screen bounds, including canvas zoom, and freezes the viewport for the gesture. Invalid numeric configurations display an error instead of silently repairing saved params.

## Validation

- `npm run build`: passed (existing large-bundle warnings only).
- Vitest: 40 passed across imageCropGeometry, imageCropPixels, composeGeometry and staleness.
- `npx playwright test e2e/image-crop.spec.ts`: 7 passed.
- `node e2e/image-crop-live.mjs`: actual gateway at port 8828, no request mocks; existing validation workflow `1f9ec9d4-e851-4fbc-828e-5940d3f22cfa`, node `unequal`.
- Actual pointer drag and all eight resize handles exercised. Inspector → viewer and viewer → Inspector values matched. Autosaved params matched. Job ID remained unchanged until explicit rerun; stale run control changed to “重新运行”.
- Final paddings L/T/R/B: `65 / 43 / 20 / 3`. Input `320×240`, output `235×194`; downloaded job output matches the original-image crop **pixel for pixel**.
- 8828 was not restarted. Unrelated concurrent WIP was neither edited nor staged.

Evidence is in `/cloud/cloud-ssd1/Kiri4DGS/hololab/tmp/image-crop-viewer/`:

- `01-visible.png`: input and eight handles.
- `02-dragging.png`: real pointer gesture in progress.
- `03-written-light.png`, `03-written-dark.png`: committed pads, drift and Inspector synchronization.
- `04-job-output.png`: completed job output.
- `source.png`, `output.png`, `evidence.json`: downloaded pixels, final params, job identifiers and exact comparison result.

Open the workflow at `http://127.0.0.1:8828/w/1f9ec9d4-e851-4fbc-828e-5940d3f22cfa`.
