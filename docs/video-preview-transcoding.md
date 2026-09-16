# Video-preview transcoding (`/_preview` endpoint)

## Why this exists

The video-grid viewer paints one `<video>` per tile so a
`video-array-source` drawer can show all cameras of a multi-cam
dataset at once. Streaming the *source* video into each tile makes the
frontend the wrong side of an arithmetic problem:

- cook_spinach ships as 21 × 2704 × 2028 / 30 fps / H.264 clips
  (~55 MiB each, ~1.1 GiB total)
- No browser has 21 hardware H.264 decoders to hand out at once —
  Chrome tops out around 4–8 concurrent HW streams before spilling
  to software decode, and software 2.7K at 30 fps chokes even on a
  fast desktop CPU
- Empirically the pre-proxy build stalled: 21 tiles at `currentTime =
  0` after 1.5 s of `.play()` (see `previews.tsx` history); the
  browser was busy negotiating decoders and never delivered frames

The fix is to keep the source bytes on the server side and hand the
browser a *small* proxy stream per tile. Same pattern the file server
already uses for thumbnails (`/_thumb`) — one on-demand ffmpeg run
per (source, size, fps), cached on disk, served through the ordinary
gateway proxy.

## Endpoint

```
GET /_preview/{W}x{H}/{sub:path}?fps=N
```

Same shape as `/_thumb`. Emits `video/mp4`.

- `W`, `H`: target box (aspect-preserved with letterbox padding, matches
  the thumb behavior). Guardrail: 1..1024 on each axis
- `fps`: 1..60, default 15
- Cached under `{workspace_root}/.hololab-previews/{sha256(source path,
  mtime, WxH, fps)[:32]}.mp4`
- Cache invalidates automatically when the source file's mtime changes
- Concurrent transcodes across the whole process are capped at
  `_PREVIEW_TRANSCODE_CONCURRENCY = 4` so a 21-tile grid opening cold
  doesn't spawn 21 ffmpeg processes at once

ffmpeg invocation:

```
ffmpeg -nostdin -loglevel error
       -i <source>
       -vf "scale=W:H:force_original_aspect_ratio=decrease,
            pad=W:H:(ow-iw)/2:(oh-ih)/2:color=black"
       -r <fps>
       -c:v libx264 -preset ultrafast -profile:v baseline -pix_fmt yuv420p -crf 28
       -an
       -movflags +faststart
       -y <cache>.tmp.mp4
```

Rationale for each flag:

- `libx264 -preset ultrafast` — encode is the hot path (source decode
  is bulk-throughput). Ultrafast is ~5× slower than `veryslow` for
  ~10 % worse compression; at these bitrates + one-time cost, we want
  encode wall time, not filesize
- `-profile:v baseline` — the most permissive HW decode path across
  Chromium, Firefox, Safari, mobile. Higher profiles buy compression
  ratio we don't need for 320 × 180
- `-pix_fmt yuv420p` — Safari refuses to decode `yuv444p`; being
  explicit avoids surprises when ffmpeg picks up an unusual pixel
  format from a producer's rendering pipeline
- `-crf 28` — visually indistinguishable from lossless at 320 × 180
  because the tile is 50–80 px on screen. Files land near 100 KiB /
  clip
- `-an` — grid tiles play muted (audio from 21 cameras would be
  chaos), so encoding audio bytes we throw away is waste
- `-movflags +faststart` — moves the `moov` box to the front of the
  file so `<video preload="metadata">` returns something playable
  within the first few KiB. Without this the browser blocks on the
  whole download to learn the duration
- `-r <fps>` — grid tiles are 50–80 px; the eye can't tell 15 fps
  from 30 fps at that size, and halving the framerate roughly halves
  the encode cost + output size

## Measured cost (cook_spinach, 21 × 2.7K / 30 fps / 10 s clips)

Machine: 16-core x86_64, no GPU accel for the encode.

| scenario                                | wall time | notes                                                                          |
| --------------------------------------- | --------- | ------------------------------------------------------------------------------ |
| single cold transcode                   | ~1.1 s    | ffmpeg subprocess spawn to `<video>` header available on the wire              |
| 21 cold transcodes, serial              | ~23.7 s   | sequential curl loop; each ~1.1 s                                              |
| 21 cold transcodes, parallel (cap=4)    | ~16.4 s   | server-side semaphore serializes ffmpeg into ceil(21/4)=6 waves                |
| all warm (any concurrency)              | ~2 ms     | `FileResponse` of cached MP4                                                   |
| output size                             | ~100 KiB  | / clip, ~2.0 MiB for all 21                                                    |

So the *first* open of a 21-camera drawer costs ~16 s of transcoding
wall time (progressively — tiles appear as their preview finishes).
Every subsequent visit is warm-cache instant.

## Frontend integration

`previews.tsx`:

- Grid tiles → `<video src={node}/_preview/{PREVIEW_W}x{PREVIEW_H}/...?fps={PREVIEW_FPS}`
- Zoom overlay → `<video src={node}/{sub}` (full-res source; only one
  decoder is active while zoomed)

Both share the same sync bus (`useVideoArraySync`), so play/pause and
scrub still act on every registered element regardless of which URL
family it points at.

Verified via Playwright on the classic-STG cook_spinach drawer:

- All 21 tile URLs are `/_preview/…`, zero point at the raw source
- Zoom URL is the raw source
- After play, all 21 tiles advance in lockstep (`drift = 0.000 s`),
  vs the pre-proxy build where none advanced
- Warm-path bar arms in <500 ms

## Concurrency-cap trade-off

The semaphore is set to 4. Why not higher?

- Each `libx264 ultrafast` process spawns ~9 encoder threads by default
- 4 concurrent processes × 9 threads = ~36 thread-slots against 16
  physical cores = ~2× oversubscription
- Bumping to 8-way parallel would use ~72 thread-slots on 16 cores,
  which past experience says hurts total throughput more than it
  helps individual-request latency
- Starting drawers on other workflows should not steal the entire CPU
  from a foreground render or a running job

If a single-user machine wanted faster cold-start it could bump this
constant (or expose it via config); we haven't seen the need.

## Fallbacks considered and rejected

**Snapshot-frame polling.** Grid tiles as `<img>` refreshed 6–8×/s
via `/_thumb?at={t}`. Zero client-side decoder cost. Rejected because
every frame refresh is another ffmpeg subprocess on the node: 21 tiles
× 8 fps = 168 ffmpeg spawns per second is a much bigger server cost
than a one-time transcode. Would need a pre-extracted frame directory
(not just an on-demand endpoint) to be viable — bigger change than the
proxy transcode.

**Pack-produced sidecar previews.** Have each pack that emits a
`video-source` / `video-source-array` handle also emit a
`.preview.mp4` alongside. Rejected because it (a) changes pack
manifest semantics for every producer, (b) doesn't help pre-existing
runs, and (c) rebuilds don't invalidate cleanly. Central endpoint
with mtime-keyed cache is strictly better.

**Rate-limit browser-side.** Cap the number of active grid `<video>`
elements at, say, 9, and rotate through the rest as static
thumbnails. Rejected because the whole point of the multi-cam grid is
seeing every camera at once; a 9-of-21 rotation defeats the design.

## Extending

- New viewers that want video preview: use `/_preview/` with your own
  dims / fps (subject to the guardrails). Don't reach for the source
  URL from a grid context
- New tag → viewer mappings: see `hololab/gateway/tag_viewers.py`
- If a dataset's clips are already tiny (say ≤ 480 × 270), the
  overhead of transcoding may not pay for itself. There's no code
  path today that skips the endpoint on "small enough" sources — add
  one if the need appears
