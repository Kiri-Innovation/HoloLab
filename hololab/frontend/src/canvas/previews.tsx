// In-canvas preview drawers.
//
// Each viewer takes a fully-resolved fetch URL (the gateway's
// ``/proxy/{node}/...`` sub-path, plus an optional ``member`` for
// dir-storage handles) and renders the underlying content inline.
//
// Design constraints (per the user's UX brief):
//   * Dark background with the content centred as the visual focus.
//   * Flat, no gradients, no glow.
//   * Sized to fit within the AlgorithmNode's expand slot without a modal.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getHandleSummary } from "../api";
import type {
  ComputeNode,
  HandleSummary,
  HandleSummaryEntry,
  OutputPreviewSpec,
} from "../wire";
import { OpenInCocoderButton } from "./OpenInCocoderButton";

// ---------------------------------------------------------------------------
// Common shell
// ---------------------------------------------------------------------------

const PREVIEW_SHELL: React.CSSProperties = {
  background: "var(--inverse-surface)",
  color: "var(--text-on-dark)",
  padding: "var(--space-2)",
  borderRadius: "var(--radius-md)",
  overflow: "hidden",
  fontFamily: "var(--font-sans)",
};

/** Loading / error / empty pill with the same visual weight for all viewers. */
function Status({ text, kind }: { text: string; kind: "loading" | "error" | "info" }) {
  const colour =
    kind === "error" ? "var(--error)" : kind === "loading" ? "var(--info)" : "var(--neutral)";
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        minHeight: 60,
        color: colour,
        fontSize: 11,
      }}
    >
      {text}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Entry point — dispatch on the viewer name.
// ---------------------------------------------------------------------------

export interface PreviewProps {
  spec: OutputPreviewSpec;
  // Base URL of the handle. For file-storage handles this IS the file
  // URL. For dir-storage the viewer appends ``/`` + spec.member.
  baseUrl: string;
  storage: "dir" | "file";
  // Handle ID — needed by the video-grid viewer to fetch the directory
  // summary and by size-gated viewers that want ``size_bytes``. Optional
  // to keep the single-URL viewers callable without one.
  handleId?: string;
  // Producing node's local absolute path — the *dir* path when
  // ``storage === "dir"``. Only the video-grid's zoom overlay
  // currently uses it (to compose a per-entry absolute path for the
  // Cobrowser "Open in Cocoder" button). Other viewers ignore it.
  absolutePath?: string;
  // The compute node that produced the handle. Passed to the
  // Cobrowser "Open in Cocoder" button so it can look up
  // ``flops_executor_id`` at render time and know which node to
  // name in the "not configured" guide text.
  producingNode?: ComputeNode | null;
}

export function Preview({
  spec,
  baseUrl,
  storage,
  handleId,
  absolutePath,
  producingNode,
}: PreviewProps) {
  // Resolve the final URL once so each viewer has a plain string to work with.
  const url =
    storage === "dir" && spec.member && spec.viewer !== "video-grid"
      ? `${baseUrl.replace(/\/$/, "")}/${spec.member.replace(/^\//, "")}`
      : baseUrl;

  switch (spec.viewer) {
    case "splatv":
      return <SplatvPreview url={url} />;
    case "video":
      return <VideoPreview url={url} />;
    case "image":
      return <ImagePreview url={url} />;
    case "text":
      return <TextPreview url={url} />;
    case "video-grid":
      if (!handleId) {
        return (
          <div style={PREVIEW_SHELL}>
            <Status text="video-grid needs a handle id (not provided)" kind="error" />
          </div>
        );
      }
      return (
        <VideoGridPreview
          baseUrl={baseUrl}
          handleId={handleId}
          memberGlob={spec.member}
          dirAbsolutePath={absolutePath}
          producingNode={producingNode}
        />
      );
    default: {
      // Compile-time exhaustiveness check: if someone widens
      // ``OutputPreviewSpec.viewer`` without adding a switch case here,
      // tsc rejects the assignment to ``never`` and the build fails.
      // Runtime still renders a labelled error (in case a *server*
      // rolls out a new viewer name before the frontend does).
      const unhandled: never = spec.viewer;
      return (
        <div style={PREVIEW_SHELL}>
          <Status text={`unknown viewer: ${unhandled as string}`} kind="error" />
        </div>
      );
    }
  }
}

// ---------------------------------------------------------------------------
// Concrete viewers
// ---------------------------------------------------------------------------

function VideoPreview({ url }: { url: string }) {
  return (
    <div style={PREVIEW_SHELL}>
      <video
        src={url}
        controls
        playsInline
        style={{
          display: "block",
          width: "100%",
          maxHeight: 260,
          background: "var(--media-surface)",
          borderRadius: "var(--radius-sm)",
        }}
      />
    </div>
  );
}

function ImagePreview({ url }: { url: string }) {
  return (
    <div style={PREVIEW_SHELL}>
      {/* Kept centred on the dark shell so the image reads as the focus. */}
      <img
        src={url}
        alt="preview"
        style={{
          display: "block",
          margin: "0 auto",
          maxWidth: "100%",
          maxHeight: 260,
          borderRadius: "var(--radius-sm)",
        }}
      />
    </div>
  );
}

// Cap the fetched text so a huge log doesn't hang the browser.
const TEXT_MAX_BYTES = 64 * 1024;

function TextPreview({ url }: { url: string }) {
  const [state, setState] = useState<
    { kind: "loading" } | { kind: "ok"; body: string; truncated: boolean } | { kind: "err"; message: string }
  >({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // Range request to cap the download volume at TEXT_MAX_BYTES.
        const r = await fetch(url, {
          headers: { Range: `bytes=0-${TEXT_MAX_BYTES - 1}` },
        });
        if (!r.ok && r.status !== 206) {
          throw new Error(`HTTP ${r.status}`);
        }
        const body = await r.text();
        if (cancelled) return;
        setState({
          kind: "ok",
          body,
          truncated: body.length >= TEXT_MAX_BYTES,
        });
      } catch (e) {
        if (cancelled) return;
        setState({ kind: "err", message: (e as Error).message });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [url]);

  if (state.kind === "loading") return <div style={PREVIEW_SHELL}><Status text="loading…" kind="loading" /></div>;
  if (state.kind === "err")
    return (
      <div style={PREVIEW_SHELL}>
        <Status text={`load failed: ${state.message}`} kind="error" />
      </div>
    );
  return (
    <div style={PREVIEW_SHELL}>
      <pre
        style={{
          margin: 0,
          fontSize: 11,
          lineHeight: 1.45,
          fontFamily:
            'ui-monospace, SFMono-Regular, Menlo, Monaco, "Cascadia Code", monospace',
          color: "var(--text-on-dark)",
          background: "transparent",
          maxHeight: 260,
          overflow: "auto",
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
        }}
      >
        {state.body}
      </pre>
      {state.truncated && (
        <div style={{ fontSize: "var(--fs-micro)", color: "var(--inverse-muted)", marginTop: "var(--space-1)" }}>
          truncated at {TEXT_MAX_BYTES / 1024} KiB · download the full file for the rest
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SplatV: decode the header, show a metadata card.
//
// The file layout is: magic ``0x674B`` (2 bytes, little-endian) + 2 pad
// bytes + json_len (uint32 LE) + JSON header (variable) + texture bytes.
// The JSON header carries texture dimensions and (for our lite converter)
// a cameras array. A full WebGL 4D-splat renderer is future work — this
// info card proves the preview data-plane and gives at-a-glance
// confirmation that the artifact came out sane.
// ---------------------------------------------------------------------------

interface SplatvHeader {
  magicHex: string;
  jsonLen: number;
  totalSize: number;
  meta: unknown;
  gaussianCount: number | null;
  texWidth: number | null;
  texHeight: number | null;
  cameraCount: number | null;
}

async function fetchSplatvHeader(url: string): Promise<SplatvHeader> {
  // Fetch just enough for the JSON header. 128 KiB is comfortably over the
  // ~74 KiB we've observed on real STG models; the fallback re-fetches
  // more if the header claims otherwise.
  const HEAD_PROBE = 128 * 1024;
  const r1 = await fetch(url, { headers: { Range: `bytes=0-${HEAD_PROBE - 1}` } });
  if (!r1.ok && r1.status !== 206) throw new Error(`HTTP ${r1.status}`);
  let buf = new Uint8Array(await r1.arrayBuffer());

  const dv0 = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
  const magic = dv0.getUint16(0, true);
  const jsonLen = dv0.getUint32(4, true);

  if (8 + jsonLen > buf.byteLength) {
    // The header is bigger than our probe — re-fetch the exact range.
    const r2 = await fetch(url, { headers: { Range: `bytes=0-${8 + jsonLen - 1}` } });
    if (!r2.ok && r2.status !== 206) throw new Error(`HTTP ${r2.status}`);
    buf = new Uint8Array(await r2.arrayBuffer());
  }

  const jsonBytes = buf.subarray(8, 8 + jsonLen);
  const jsonText = new TextDecoder("utf-8").decode(jsonBytes);
  let meta: unknown = null;
  try {
    meta = JSON.parse(jsonText);
  } catch {
    meta = jsonText.slice(0, 200);
  }

  // Total file size via a HEAD (some servers refuse — fall back to
  // ``content-range`` if the initial partial GET returned one).
  let totalSize = 0;
  const cr = r1.headers.get("content-range");
  if (cr) {
    const m = /\/(\d+)$/.exec(cr);
    if (m) totalSize = Number.parseInt(m[1], 10);
  }
  if (!totalSize) {
    const cl = r1.headers.get("content-length");
    if (cl) totalSize = Number.parseInt(cl, 10);
  }

  // Best-effort field extraction from the meta JSON.
  const firstEntry =
    Array.isArray(meta) && meta.length > 0 && typeof meta[0] === "object" && meta[0]
      ? (meta[0] as Record<string, unknown>)
      : null;
  const texWidth = firstEntry ? (firstEntry.texwidth as number | undefined) ?? null : null;
  const size = firstEntry ? (firstEntry.size as number | undefined) ?? null : null;
  // Each gaussian in the ``lite`` splatv format occupies 4 texels of
  // stride-4 RGBA8. Height = size / (texwidth * 4). Gaussians = size / 16.
  const texHeight = texWidth && size ? size / (texWidth * 4) : null;
  const gaussianCount = size ? Math.floor(size / 16) : null;
  const cameraCount =
    firstEntry && Array.isArray(firstEntry.cameras)
      ? (firstEntry.cameras as unknown[]).length
      : null;

  return {
    magicHex: `0x${magic.toString(16)}`,
    jsonLen,
    totalSize,
    meta,
    gaussianCount,
    texWidth,
    texHeight,
    cameraCount,
  };
}

function humanBytes(n: number | null | undefined): string {
  if (!n || n <= 0) return "?";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KiB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MiB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GiB`;
}

function SplatvPreview({ url }: { url: string }) {
  const [state, setState] = useState<
    | { kind: "loading" }
    | { kind: "ok"; header: SplatvHeader }
    | { kind: "err"; message: string }
  >({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const header = await fetchSplatvHeader(url);
        if (!cancelled) setState({ kind: "ok", header });
      } catch (e) {
        if (!cancelled) setState({ kind: "err", message: (e as Error).message });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [url]);

  if (state.kind === "loading") return <div style={PREVIEW_SHELL}><Status text="decoding splatv header…" kind="loading" /></div>;
  if (state.kind === "err")
    return (
      <div style={PREVIEW_SHELL}>
        <Status text={`load failed: ${state.message}`} kind="error" />
      </div>
    );

  const h = state.header;
  return (
    <div style={PREVIEW_SHELL}>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "auto 1fr",
          gap: "4px 12px",
          fontSize: 11,
          maxHeight: 220,
        }}
      >
        <Field label="magic" value={h.magicHex} />
        <Field label="file size" value={humanBytes(h.totalSize)} />
        <Field label="header" value={`${humanBytes(h.jsonLen)} JSON`} />
        <Field
          label="texture"
          value={h.texWidth && h.texHeight ? `${h.texWidth} × ${Math.round(h.texHeight)}` : "?"}
        />
        <Field label="gaussians" value={h.gaussianCount != null ? h.gaussianCount.toLocaleString() : "?"} />
        <Field label="cameras" value={h.cameraCount != null ? String(h.cameraCount) : "?"} />
      </div>
      <div style={{ marginTop: "var(--space-2)", fontSize: "var(--fs-micro)", color: "var(--inverse-muted)" }}>
        Interactive 4D viewer is a follow-up. Download{" "}
        <a href={url} style={{ color: "var(--info)" }} target="_blank" rel="noreferrer">
          the .splatv
        </a>{" "}
        to open in an external SplaTV viewer.
      </div>
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <>
      <div style={{ color: "var(--inverse-muted)", textAlign: "right" }}>{label}</div>
      <div style={{ color: "var(--text-on-dark)", fontFamily: "var(--font-mono)" }}>
        {value}
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Video-grid: multi-camera preview with shared playback controls.
//
// The tiles are pure video frames — no filenames, no play chevrons, no
// metadata footer. That info lives in a hover tooltip so a dense 5×5
// grid stays quiet and readable at a glance. A single shared control
// bar below the grid drives all N videos in lockstep so a 21-camera
// dataset scrubs as one clip. Click a tile to zoom into it (large view
// with a ← back pill, Esc also exits); the shared bar keeps running
// so grid ↔ zoom transitions are continuous.
//
// Sync model
// ~~~~~~~~~~
//   * Each mounted video (grid tile + optional zoom overlay) registers
//     into a ``Map<id, HTMLVideoElement>`` ref bag. play / pause / seek
//     iterate the map so all cameras stay in step.
//   * Exactly one "master" reports ``timeupdate`` back into React state
//     (grid tile 0 normally; the zoom video while zoomed). That caps
//     bar-repaint rate at ~4 Hz instead of ~4×N Hz on a big array.
//   * Duration = ``max`` across all loaded metadata. Multi-cam
//     datasets usually have identical durations; when they don't,
//     individual tiles freeze on their last frame past their own end
//     while the shared bar keeps advancing through the longest clip.
//     That's less confusing than the bar stopping while most videos
//     still have footage.
//   * Muted always — 21 concurrent audio tracks would be chaos, and
//     muted playback also side-steps the autoplay-with-audio gate.
//
// Concurrency budget
// ~~~~~~~~~~~~~~~~~~
//   All N tile videos decode concurrently while playing. Muted HW-
//   decoded H.264 from a local endpoint sits comfortably inside a
//   modern browser's decoder pool — the cook_spinach dataset (21×
//   ~7 MiB clips) plays smoothly in tests. Zoom adds one more
//   concurrent decoder for the enlarged view. If a future dataset
//   stresses this budget we can pause grid tiles on zoom and drive the
//   grid off ``requestVideoFrameCallback`` snapshots; not warranted
//   yet.
// ---------------------------------------------------------------------------

// Frontend renders tiles at ~160x90 CSS px on a standard-DPI display; the
// backend generates thumbs at 2x for retina. ``pad`` in the ffmpeg filter
// letterboxes so the aspect stays clean regardless of source.
const THUMB_W = 320;
const THUMB_H = 180;
const THUMB_AT_SECONDS = 1.0;

// Low-res proxy video used for grid tiles. The full-res source (~2.7K
// / 30 fps on typical multi-cam datasets) is fine for the zoom view
// where only ONE decoder is active, but 21 concurrent HW-decoded 2.7K
// streams do not fit in any browser's decoder pool — we've seen the
// grid stall to a slideshow. The node's ``/_preview`` endpoint
// transcodes to H.264 baseline at these dims + fps, caches on disk,
// and streams the ~100 KiB output. Bigger than the still thumbnail
// but two orders of magnitude smaller than the source. Grid tiles use
// this URL; the zoom overlay keeps the full-res source (one decoder
// is fine).
const PREVIEW_W = 320;
const PREVIEW_H = 180;
const PREVIEW_FPS = 15;

const VIDEO_EXTS = [".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"];

function isVideoName(name: string): boolean {
  const n = name.toLowerCase();
  return VIDEO_EXTS.some((ext) => n.endsWith(ext));
}

/** Minimal ``*``/``?`` glob → RegExp. We deliberately keep this small
 *  (no character classes, no braces) — pack authors only ever want to
 *  narrow by extension, e.g. ``*.mp4``.
 */
function globToRegExp(glob: string): RegExp {
  const escaped = glob.replace(/[.+^${}()|[\]\\]/g, "\\$&");
  const withStar = escaped.replace(/\*/g, ".*").replace(/\?/g, ".");
  return new RegExp(`^${withStar}$`, "i");
}

function filterEntries(
  entries: HandleSummaryEntry[],
  glob: string | null | undefined,
): HandleSummaryEntry[] {
  const kept = entries.filter((e) => !e.is_dir && !e.name.startsWith("."));
  if (glob && glob.trim()) {
    const re = globToRegExp(glob.trim());
    return kept.filter((e) => re.test(e.name));
  }
  return kept.filter((e) => isVideoName(e.name));
}

interface VideoGridProps {
  baseUrl: string;
  handleId: string;
  memberGlob: string | null | undefined;
  // Cobrowser integration — the video-array-source dir's absolute
  // path on the producing node + the compute node itself. Used to
  // build a per-tile "Open in Cocoder" call inside the zoom overlay.
  // Undefined / null when caller didn't thread them through — the
  // zoom overlay simply hides the button in that case.
  dirAbsolutePath?: string;
  producingNode?: ComputeNode | null;
}

// Gap between tiles (CSS px). Kept small so a dense 5×5 layout doesn't
// waste half its area on gutter.
const GRID_GAP = 6;

// Minimum height (CSS px) the grid area grows to when a tile is
// zoomed. Applied as ``minHeight`` so datasets with tall natural grids
// (many rows) keep their taller layout, while sparse arrays get a
// comfortable zoom canvas instead of a cramped strip.
const ZOOM_MIN_H = 220;

/** Layout math for the non-scrolling grid.
 *
 *  Given ``N`` entries and a container width ``W``, pick a column count
 *  that keeps tiles as large as possible while ensuring the resulting
 *  ``ceil(N/cols) × tile_h`` block fits inside the container's height
 *  budget (if one is provided).
 *
 *  Tile aspect is fixed at 16:9 to match the ffmpeg-generated thumbs.
 *  When ``W`` is unknown (first render, before ResizeObserver fires) we
 *  return a sensible default so React doesn't paint a zero-size grid.
 */
function computeGridLayout(
  n: number,
  containerW: number,
  containerH: number | null,
): { cols: number; rows: number; tileW: number; tileH: number } {
  const w = Math.max(80, containerW || 320);
  const safeN = Math.max(1, n);
  // Pick cols by an aspect-ratio heuristic: ``sqrt(N)`` gives a
  // roughly-square grid of 16:9 tiles, which lines up nicely with the
  // user's 21-camera → 5×5 example. When a container height budget is
  // supplied, we then walk cols upward if the natural layout would
  // overflow.
  let cols = Math.min(safeN, Math.max(1, Math.ceil(Math.sqrt(safeN))));
  const tileFor = (c: number) => {
    const tw = (w - GRID_GAP * (c - 1)) / c;
    const th = tw * (9 / 16);
    const r = Math.ceil(safeN / c);
    const totalH = r * th + GRID_GAP * (r - 1);
    return { tw, th, r, totalH };
  };
  if (containerH !== null) {
    while (cols < safeN && tileFor(cols).totalH > containerH) {
      cols += 1;
    }
  }
  const { tw, th, r } = tileFor(cols);
  return { cols, rows: r, tileW: tw, tileH: th };
}

/** Track an element's CSS-pixel width via ResizeObserver.
 *
 *  Height isn't tracked — the grid is content-sized, so the outer
 *  container's height is *determined by* the layout we compute, not
 *  the other way round. Callers that need a height budget pass one in
 *  explicitly (``computeGridLayout``'s third arg).
 */
function useElementWidth(): [(el: HTMLDivElement | null) => void, number] {
  const [w, setW] = useState<number>(0);
  const observerRef = useRef<ResizeObserver | null>(null);
  const setRef = (el: HTMLDivElement | null) => {
    if (observerRef.current) {
      observerRef.current.disconnect();
      observerRef.current = null;
    }
    if (!el) return;
    setW(el.clientWidth);
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) {
        setW(Math.round(e.contentRect.width));
      }
    });
    ro.observe(el);
    observerRef.current = ro;
  };
  return [setRef, w];
}

// ID used to key the zoom overlay's video into the shared refs map.
const ZOOM_REF_ID = "__zoom__";

/** Format a seconds count as ``M:SS`` (or ``H:MM:SS`` past an hour). */
function formatTime(t: number): string {
  if (!Number.isFinite(t) || t < 0) t = 0;
  const total = Math.floor(t);
  const s = total % 60;
  const m = Math.floor(total / 60) % 60;
  const h = Math.floor(total / 3600);
  const ss = s.toString().padStart(2, "0");
  if (h > 0) return `${h}:${m.toString().padStart(2, "0")}:${ss}`;
  return `${m}:${ss}`;
}

/** Shared sync bus for the multi-camera video array.
 *
 *  Each video (grid tiles + the optional zoom overlay) registers into a
 *  single ``Map<id, HTMLVideoElement>`` via ``register``. Public
 *  actions (``togglePlay``, ``seek``) iterate the map, so all cameras
 *  move together. Duration is tracked per-id so a mismatched dataset
 *  still reports a coherent max across the array.
 */
interface VideoArraySync {
  playing: boolean;
  currentTime: number;
  duration: number;
  register: (id: string, el: HTMLVideoElement | null) => void;
  reportDuration: (id: string, d: number) => void;
  reportMasterTime: (t: number) => void;
  reportEnded: () => void;
  play: () => void;
  pause: () => void;
  togglePlay: () => void;
  seek: (t: number) => void;
}

function useVideoArraySync(): VideoArraySync {
  const [playing, setPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [durations, setDurations] = useState<Record<string, number>>({});

  // Refs shadow state so the register callback (created once) sees the
  // latest values without needing to be re-created on every tick.
  const playingRef = useRef(playing);
  playingRef.current = playing;
  const currentTimeRef = useRef(currentTime);
  currentTimeRef.current = currentTime;
  // Duration is derived state (max of reported per-tile durations);
  // shadow it via a ref for the play()/end-of-video guard below.
  const durationRef = useRef(0);

  const refs = useRef<Map<string, HTMLVideoElement>>(new Map());

  const register = useCallback((id: string, el: HTMLVideoElement | null) => {
    if (el === null) {
      refs.current.delete(id);
      return;
    }
    const prev = refs.current.get(id);
    refs.current.set(id, el);
    // *Only* write ``currentTime`` on a truly fresh registration —
    // same-element re-registration (a stable ref-callback should
    // suppress those, but a parent that inadvertently rebuilds
    // ``setRef`` on each render will fire this path ~15 Hz through
    // the master's timeupdate feedback). Writing here on every tick
    // seeks the video backwards to whatever value React was holding
    // at commit time, one frame stale, which manifests to the user
    // as "playback freezes at the last seek target and only twitches
    // between two frames." The instrumented diagnostic saw 1583
    // writes in 3 s of "playback" on a single tile.
    if (prev === el) return;
    // Only sync the fresh element's currentTime if it has drifted
    // meaningfully. Autoplay-blocked mounts land with currentTime=0
    // and shared state might already be several seconds in, in
    // which case we do want to catch up.
    try {
      if (Math.abs(el.currentTime - currentTimeRef.current) > 0.3) {
        el.currentTime = currentTimeRef.current;
      }
    } catch {
      // Some browsers reject seeking before metadata; ignore.
    }
    if (playingRef.current) {
      void el.play().catch(() => {
        // Autoplay may be blocked; user can click play manually.
      });
    } else {
      el.pause();
    }
  }, []);

  const reportDuration = useCallback((id: string, d: number) => {
    if (!Number.isFinite(d) || d <= 0) return;
    setDurations((prev) => (prev[id] === d ? prev : { ...prev, [id]: d }));
  }, []);

  const reportMasterTime = useCallback((t: number) => {
    setCurrentTime(t);
  }, []);

  // Apply playing state to every currently registered element.
  useEffect(() => {
    for (const v of refs.current.values()) {
      if (playing) {
        void v.play().catch(() => {});
      } else {
        v.pause();
      }
    }
  }, [playing]);

  const seek = useCallback((t: number) => {
    setCurrentTime(t);
    for (const v of refs.current.values()) {
      try {
        v.currentTime = t;
      } catch {
        // See register(): pre-metadata seek can throw on some browsers.
      }
    }
  }, []);

  // pause/play both touch the ``<video>`` elements *synchronously*
  // in addition to updating React state. The effect that iterates
  // ``refs`` on ``playing`` change fires one commit later — 15-30 ms
  // — and a 21-tile grid at 15 fps can slip ~5-30 rvfc frames past
  // that window. The user's headline ask was "drag pauses
  // immediately"; the effect is idempotent so calling it again on
  // the next commit is harmless.
  const pause = useCallback(() => {
    setPlaying(false);
    for (const v of refs.current.values()) {
      try {
        v.pause();
      } catch {}
    }
  }, []);

  // ``play()`` handles the "user hit play after the video ended"
  // case by rewinding to 0 first — every mainstream player does
  // this, so leaving currentTime pinned at duration and just
  // flipping ``playing`` back to true (which yields no visible
  // advance) would be surprising.
  const play = useCallback(() => {
    if (
      durationRef.current > 0 &&
      currentTimeRef.current >= durationRef.current - 0.05
    ) {
      setCurrentTime(0);
      for (const v of refs.current.values()) {
        try {
          v.currentTime = 0;
        } catch {}
      }
    }
    setPlaying(true);
    for (const v of refs.current.values()) {
      void v.play().catch(() => {});
    }
  }, []);

  const togglePlay = useCallback(() => {
    if (playingRef.current) pause();
    else play();
  }, [pause, play]);

  // Master's onEnded → flip ``playing`` to false so the button
  // reflects reality (was still showing ❚❚ pre-fix even though
  // every video had already hit its own last frame). Also snap
  // ``currentTime`` to ``duration``: Chrome's last ``timeupdate``
  // before an ``ended`` event lands at ~duration - keyframe_gap
  // rather than exactly at duration, which meant the derived
  // ``atEnd = ct >= dur - 0.05`` check stayed false and the button
  // showed ▶ (paused) instead of ↻ (replay).
  const reportEnded = useCallback(() => {
    setPlaying(false);
    if (durationRef.current > 0) setCurrentTime(durationRef.current);
  }, []);

  const duration = useMemo(() => {
    const vals = Object.values(durations);
    return vals.length === 0 ? 0 : Math.max(...vals);
  }, [durations]);
  durationRef.current = duration;

  return {
    playing,
    currentTime,
    duration,
    register,
    reportDuration,
    reportMasterTime,
    reportEnded,
    play,
    pause,
    togglePlay,
    seek,
  };
}

function VideoGridPreview({
  baseUrl,
  handleId,
  memberGlob,
  dirAbsolutePath,
  producingNode,
}: VideoGridProps) {
  const [state, setState] = useState<
    { kind: "loading" } | { kind: "ok"; summary: HandleSummary } | { kind: "err"; message: string }
  >({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const summary = await getHandleSummary(handleId);
        if (!cancelled) setState({ kind: "ok", summary });
      } catch (e) {
        if (!cancelled) setState({ kind: "err", message: (e as Error).message });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [handleId]);

  const videoEntries = useMemo(() => {
    if (state.kind !== "ok") return [];
    return filterEntries(state.summary.fields.entries ?? [], memberGlob);
  }, [state, memberGlob]);

  const [gridRef, gridW] = useElementWidth();
  const layout = useMemo(
    () => computeGridLayout(videoEntries.length, gridW, null),
    [videoEntries.length, gridW],
  );

  const sync = useVideoArraySync();
  const [zoomedIdx, setZoomedIdx] = useState<number | null>(null);
  const [hovered, setHovered] = useState(false);

  // Reset zoom + sync when the entry set changes (e.g. handle switch).
  useEffect(() => {
    setZoomedIdx(null);
  }, [handleId]);

  // Esc key exits zoom mode without needing to reach the ← pill.
  useEffect(() => {
    if (zoomedIdx === null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setZoomedIdx(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [zoomedIdx]);

  useVideoArrayKeyboard(sync, hovered);

  if (state.kind === "loading") {
    return (
      <div style={PREVIEW_SHELL}>
        <Status text="loading directory…" kind="loading" />
      </div>
    );
  }
  if (state.kind === "err") {
    return (
      <div style={PREVIEW_SHELL}>
        <Status text={`load failed: ${state.message}`} kind="error" />
      </div>
    );
  }
  if (videoEntries.length === 0) {
    return (
      <div style={PREVIEW_SHELL}>
        <Status text="no matching video files" kind="info" />
      </div>
    );
  }

  const zoomEntry = zoomedIdx !== null ? videoEntries[zoomedIdx] : null;

  return (
    <div
      data-hl-video-array=""
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        ...PREVIEW_SHELL,
        position: "relative",
        overflow: "hidden",
        display: "flex",
        flexDirection: "column",
        gap: 6,
      }}
    >
      <div
        style={{
          position: "relative",
          overflow: "hidden",
          // While zoomed, grow the grid area to a comfortable canvas
          // for the enlarged video. Sparse arrays with a naturally
          // short grid gain vertical room; dense arrays keep theirs.
          minHeight: zoomedIdx !== null ? ZOOM_MIN_H : undefined,
        }}
      >
        <div
          ref={gridRef}
          data-hl-video-grid=""
          data-hl-cols={layout.cols}
          data-hl-tile-count={videoEntries.length}
          style={{
            display: "grid",
            gridTemplateColumns: `repeat(${layout.cols}, 1fr)`,
            gap: GRID_GAP,
            overflow: "hidden",
          }}
        >
          {videoEntries.map((entry, idx) => (
            <VideoTile
              key={entry.name}
              baseUrl={baseUrl}
              entry={entry}
              // Grid tile 0 is master while nothing is zoomed. When
              // zoomed, master handoff goes to the zoom overlay so its
              // playback ticks the shared bar (the grid videos keep
              // playing invisibly for a smooth back-transition).
              isMaster={zoomedIdx === null && idx === 0}
              sync={sync}
              onZoom={() => setZoomedIdx(idx)}
            />
          ))}
        </div>
        {zoomEntry !== null && (
          <ZoomOverlay
            baseUrl={baseUrl}
            entry={zoomEntry}
            sync={sync}
            onClose={() => setZoomedIdx(null)}
            dirAbsolutePath={dirAbsolutePath}
            producingNode={producingNode}
          />
        )}
      </div>
      <VideoArrayControls sync={sync} />
    </div>
  );
}

interface TileProps {
  baseUrl: string;
  entry: HandleSummaryEntry;
  isMaster: boolean;
  sync: VideoArraySync;
  onZoom: () => void;
}

function VideoTile({ baseUrl, entry, isMaster, sync, onZoom }: TileProps) {
  // Build the same-origin URLs. ``baseUrl`` is the dir's ``/proxy/{node}/{sub}``.
  // Thumb + preview endpoints are separate node routes rooted at the
  // same ``/proxy/{node}`` prefix.
  const [nodeRoot, dirSub] = useMemo(() => splitProxyBase(baseUrl), [baseUrl]);
  const encodedEntry = encodeURIComponent(entry.name);
  const memberSub = `${dirSub}/${encodedEntry}`;
  const thumbUrl = `${nodeRoot}/_thumb/${THUMB_W}x${THUMB_H}/${memberSub}?at=${THUMB_AT_SECONDS}`;
  // Grid tiles stream the low-res proxy (see PREVIEW_W/H/FPS above and
  // the node's ``/_preview`` endpoint). The full-res source is only
  // used by the ZoomOverlay, which decodes exactly one video at a
  // time.
  const previewUrl = `${nodeRoot}/_preview/${PREVIEW_W}x${PREVIEW_H}/${memberSub}?fps=${PREVIEW_FPS}`;

  const size = entry.size_bytes ?? 0;
  const tooltip = `${entry.name} · ${humanBytes(size)}`;

  // Ref-callback identity MUST be stable across parent re-renders.
  // The parent's ``sync`` object is a fresh literal each render (its
  // ``currentTime`` field is reactive state that ticks ~15 Hz through
  // the master's timeupdate). Putting ``sync`` in the useCallback
  // deps would rebuild ``setRef`` on every tick; React then detaches
  // (setRef(null)) + reattaches (setRef(el)) the ref on the video
  // element, which in the old code called ``sync.register(id, el)``
  // and wrote ``currentTime = currentTimeRef.current`` — a
  // one-frame-stale rewind that ground playback to a halt. A ref
  // indirection to the LATEST sync keeps setRef stable while still
  // reading the current callbacks off the bus.
  const syncRef = useRef(sync);
  syncRef.current = sync;
  const vidRef = useRef<HTMLVideoElement | null>(null);
  const setRef = useCallback(
    (el: HTMLVideoElement | null) => {
      vidRef.current = el;
      syncRef.current.register(entry.name, el);
    },
    [entry.name],
  );

  return (
    <div
      data-hl-tile={entry.name}
      title={tooltip}
      onClick={onZoom}
      style={{
        position: "relative",
        aspectRatio: "16 / 9",
        background: "#000",
        borderRadius: "var(--radius-sm)",
        overflow: "hidden",
        border: "1px solid var(--border, rgba(255,255,255,0.08))",
        cursor: "zoom-in",
      }}
    >
      <video
        ref={setRef}
        src={previewUrl}
        poster={thumbUrl}
        muted
        playsInline
        preload="metadata"
        onLoadedMetadata={(e) => {
          const el = e.currentTarget;
          syncRef.current.reportDuration(entry.name, el.duration);
        }}
        onTimeUpdate={
          isMaster
            ? (e) => syncRef.current.reportMasterTime(e.currentTarget.currentTime)
            : undefined
        }
        onEnded={isMaster ? () => syncRef.current.reportEnded() : undefined}
        style={{
          display: "block",
          width: "100%",
          height: "100%",
          objectFit: "cover",
          background: "#000",
          // Absent a decoded frame the browser paints the ``poster``;
          // once metadata + first frame arrive, the frame replaces it.
          pointerEvents: "none",
        }}
      />
    </div>
  );
}

interface ZoomOverlayProps {
  baseUrl: string;
  entry: HandleSummaryEntry;
  sync: VideoArraySync;
  onClose: () => void;
  // For the top-right "Open in Cocoder" button. Undefined dirPath / null
  // producing node = button hides.
  dirAbsolutePath?: string;
  producingNode?: ComputeNode | null;
}

function ZoomOverlay({
  baseUrl,
  entry,
  sync,
  onClose,
  dirAbsolutePath,
  producingNode,
}: ZoomOverlayProps) {
  const [nodeRoot, dirSub] = useMemo(() => splitProxyBase(baseUrl), [baseUrl]);
  const encodedEntry = encodeURIComponent(entry.name);
  const videoUrl = `${baseUrl.replace(/\/$/, "")}/${encodedEntry}`;
  const thumbSub = `${dirSub}/${encodedEntry}`;
  const thumbUrl = `${nodeRoot}/_thumb/${THUMB_W}x${THUMB_H}/${thumbSub}?at=${THUMB_AT_SECONDS}`;

  // Same stable-setRef pattern as VideoTile — see comment there for
  // why the ref indirection matters.
  const syncRef = useRef(sync);
  syncRef.current = sync;
  const setRef = useCallback(
    (el: HTMLVideoElement | null) => syncRef.current.register(ZOOM_REF_ID, el),
    [],
  );

  return (
    <div
      data-hl-zoom-overlay=""
      style={{
        position: "absolute",
        inset: 0,
        background: "#000",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <video
        ref={setRef}
        src={videoUrl}
        poster={thumbUrl}
        muted
        playsInline
        preload="metadata"
        onLoadedMetadata={(e) => syncRef.current.reportDuration(ZOOM_REF_ID, e.currentTarget.duration)}
        // Zoom is master while active — see VideoGridPreview render.
        onTimeUpdate={(e) => syncRef.current.reportMasterTime(e.currentTarget.currentTime)}
        onEnded={() => syncRef.current.reportEnded()}
        style={{
          display: "block",
          width: "100%",
          height: "100%",
          objectFit: "contain",
          background: "#000",
        }}
      />
      <button
        type="button"
        onClick={onClose}
        title="back to grid (Esc)"
        data-hl-zoom-back=""
        style={{
          position: "absolute",
          top: 6,
          left: 6,
          display: "inline-flex",
          alignItems: "center",
          gap: 4,
          height: 24,
          padding: "0 10px",
          background: "rgba(0,0,0,0.55)",
          color: "#fff",
          border: "1px solid rgba(255,255,255,0.18)",
          borderRadius: "var(--radius-pill)",
          fontSize: 11,
          fontWeight: 500,
          cursor: "pointer",
          backdropFilter: "blur(2px)",
        }}
      >
        ← back
      </button>
      <div
        style={{
          position: "absolute",
          top: 6,
          right: 6,
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          maxWidth: "60%",
        }}
      >
        <div
          style={{
            padding: "2px 8px",
            background: "rgba(0,0,0,0.55)",
            color: "#fff",
            border: "1px solid rgba(255,255,255,0.18)",
            borderRadius: "var(--radius-pill)",
            fontSize: 10,
            fontFamily: "var(--font-mono)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
          title={entry.name}
        >
          {entry.name}
        </div>
        {dirAbsolutePath && (
          <OpenInCocoderButton
            path={`${dirAbsolutePath.replace(/\/$/, "")}/${entry.name}`}
            computeNode={producingNode ?? null}
            onDark
          />
        )}
      </div>
    </div>
  );
}

interface ControlsProps {
  sync: VideoArraySync;
}

/** Shared playback bar: play/pause + scrub + ``current / total`` time.
 *
 *  Drives every video registered on the sync bus. Slider is disabled
 *  until at least one video has reported duration, otherwise a
 *  ``max=0`` range is meaningless. Numeric label uses tabular figures
 *  so it doesn't jitter as digits change.
 */
function VideoArrayControls({ sync }: ControlsProps) {
  const disabled = sync.duration <= 0;

  // Track the pre-drag playing state so pointerup can restore it —
  // matches YouTube / Vimeo / QuickTime: drag pauses immediately,
  // release resumes if the user had been playing. The refs mean the
  // window-level pointerup handler doesn't need ``sync`` in its deps
  // (would otherwise reattach on every timeupdate tick).
  const syncRef = useRef(sync);
  syncRef.current = sync;
  const wasPlayingRef = useRef(false);
  const draggingRef = useRef(false);

  // Local drag position. ``<input type=range>`` was a *controlled*
  // input driven by ``sync.currentTime`` — during a fast drag on a
  // 21-tile grid the ``onChange → sync.seek → 21× v.currentTime =
  // t → setCurrentTime`` chain hits ~485 currentTime writes/sec.
  // Real browsers can't commit React state fast enough under that
  // load, so React resets the DOM ``value`` to the last committed
  // ``sync.currentTime`` while the user is still dragging — the
  // browser's native drag tracking gets fought and the thumb
  // visibly sticks. Rendering the slider from ``dragValue`` while
  // ``dragValue !== null`` decouples the thumb from that chain: the
  // browser owns the thumb position during the drag, and React only
  // shows it *after* the release lands a real ``sync.currentTime``.
  const [dragValue, setDragValue] = useState<number | null>(null);

  // rAF-throttle the actual video seeks during drag. Without this
  // every ~16 ms input event fired a full 21-tile seek storm; with
  // it we coalesce to one seek per animation frame regardless of
  // how many input events land in between. Sync bar / scrub-preview
  // still feels 60 Hz because ``dragValue`` updates on every event.
  const rafRef = useRef<number | null>(null);
  const scrubTargetRef = useRef<number>(0);
  const flushScrub = useCallback(() => {
    rafRef.current = null;
    syncRef.current.seek(scrubTargetRef.current);
  }, []);

  // pointerup can fire outside the slider (user drags off the bar
  // then releases), so listen at window level. Attached once — the
  // handler reads the latest sync via syncRef.
  useEffect(() => {
    const onUp = () => {
      if (!draggingRef.current) return;
      draggingRef.current = false;
      // Flush any pending rAF seek, then commit the final drag
      // position so every registered video lands on the exact
      // release point (the throttled writes might have skipped it).
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      const finalV = scrubTargetRef.current;
      if (Number.isFinite(finalV)) {
        syncRef.current.seek(finalV);
      }
      // ``sync.seek`` set ``sync.currentTime = finalV`` on the same
      // event tick, so releasing ``dragValue`` in the same batch
      // makes React re-render with the slider still at finalV —
      // no jump.
      setDragValue(null);
      if (wasPlayingRef.current) syncRef.current.play();
    };
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
    return () => {
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
    };
  }, []);

  const atEnd = sync.duration > 0 && sync.currentTime >= sync.duration - 0.05;
  const sliderValue =
    dragValue !== null ? dragValue : Math.min(sync.currentTime, sync.duration || 0);

  return (
    <div
      data-hl-controls=""
      className="nodrag nopan"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: "2px 2px 0",
        color: "var(--text-on-dark)",
        fontSize: 11,
      }}
    >
      <button
        type="button"
        onClick={sync.togglePlay}
        disabled={disabled}
        title={sync.playing ? "pause (space)" : atEnd ? "replay (space)" : "play (space)"}
        data-hl-play=""
        data-hl-state={sync.playing ? "playing" : atEnd ? "ended" : "paused"}
        className="nodrag nopan"
        style={{
          width: 28,
          height: 28,
          padding: 0,
          background: "transparent",
          border: "1px solid var(--inverse-muted, rgba(255,255,255,0.35))",
          borderRadius: "50%",
          color: "var(--text-on-dark)",
          cursor: disabled ? "default" : "pointer",
          opacity: disabled ? 0.4 : 1,
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: sync.playing ? 10 : 12,
          lineHeight: 1,
        }}
      >
        {sync.playing ? "❚❚" : atEnd ? "↻" : "▶"}
      </button>
      <input
        type="range"
        min={0}
        max={Math.max(sync.duration, 0.01)}
        step={0.01}
        value={sliderValue}
        disabled={disabled}
        onChange={(e) => {
          const v = Number.parseFloat(e.target.value);
          scrubTargetRef.current = v;
          if (draggingRef.current) {
            // Slider tracks the drag *immediately* via dragValue;
            // media seeks queue behind a single rAF so 21 tiles
            // don't stampede.
            setDragValue(v);
            if (rafRef.current === null) {
              rafRef.current = requestAnimationFrame(flushScrub);
            }
          } else {
            // Bare click on the track (no pointerdown+drag) — commit
            // instantly. (In practice pointerdown always fires
            // first, but a keyboard-arrow-driven change is a click
            // in this sense and hitting seek() is the right thing.)
            syncRef.current.seek(v);
          }
        }}
        data-hl-scrub=""
        className="nodrag nopan"
        // Pause immediately on any interaction (click or drag) so
        // the playhead doesn't advance while the user is choosing
        // a position — resume happens on the window pointerup
        // handler above. Only ``stopPropagation`` here, never
        // ``preventDefault``: preventing the default kills the
        // browser's native range-slider drag tracking entirely.
        onPointerDown={(e) => {
          e.stopPropagation();
          if (disabled) return;
          draggingRef.current = true;
          wasPlayingRef.current = syncRef.current.playing;
          if (syncRef.current.playing) syncRef.current.pause();
          // Seed dragValue with the current playhead so the very
          // first render during drag has a defined thumb position
          // (React's re-render for setDragValue is scheduled but
          // the initial slider paint uses sliderValue derived here).
          setDragValue(syncRef.current.currentTime);
          scrubTargetRef.current = syncRef.current.currentTime;
        }}
        style={{
          flex: 1,
          cursor: disabled ? "default" : "pointer",
          accentColor: "var(--info, #4aa3ff)",
        }}
      />
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontVariantNumeric: "tabular-nums",
          minWidth: 78,
          textAlign: "right",
          color: "var(--inverse-muted)",
        }}
      >
        {formatTime(sync.currentTime)} / {formatTime(sync.duration)}
      </span>
    </div>
  );
}

/** Keyboard shortcuts on the preview area.
 *
 *   space   toggle play/pause
 *   ←       jump back 5 s
 *   →       jump forward 5 s
 *
 *  Gated by hover so shortcuts don't fire while the user is somewhere
 *  else on the canvas, and by the active-element check so typing in
 *  the workflow name / a form input doesn't accidentally scrub the
 *  video. Also runs on the capture phase so a slider-focused arrow
 *  key gets our +/- 5 s jump instead of the browser's step-by-0.01 s
 *  default.
 */
const KEYBOARD_SEEK_STEP = 5;

function useVideoArrayKeyboard(sync: VideoArraySync, hovered: boolean): void {
  const syncRef = useRef(sync);
  syncRef.current = sync;

  useEffect(() => {
    if (!hovered) return;
    const onKey = (e: KeyboardEvent) => {
      const active = document.activeElement as HTMLElement | null;
      const tag = active?.tagName?.toLowerCase();
      if (
        tag === "input" &&
        (active as HTMLInputElement | null)?.type !== "range"
      ) {
        return;
      }
      if (tag === "textarea" || active?.isContentEditable) return;

      if (e.key === " " || e.code === "Space") {
        e.preventDefault();
        syncRef.current.togglePlay();
        return;
      }
      if (e.key === "ArrowLeft") {
        e.preventDefault();
        const s = syncRef.current;
        s.seek(Math.max(0, s.currentTime - KEYBOARD_SEEK_STEP));
        return;
      }
      if (e.key === "ArrowRight") {
        e.preventDefault();
        const s = syncRef.current;
        const cap = s.duration > 0 ? s.duration : Infinity;
        s.seek(Math.min(cap, s.currentTime + KEYBOARD_SEEK_STEP));
      }
    };
    // Capture phase so a slider-focused arrow key gets us first
    // instead of the browser's step-by-``step`` (0.01 s) default.
    window.addEventListener("keydown", onKey, { capture: true });
    return () => window.removeEventListener("keydown", onKey, { capture: true });
  }, [hovered]);
}

/** Split a ``/proxy/{node}/{sub}`` URL into its node prefix and sub-path.
 *
 *  Returns ``[nodeRoot, sub]`` where ``nodeRoot`` is
 *  ``/proxy/{node_id}`` and ``sub`` is the remaining directory path with
 *  no leading slash. We need this so the video-grid tile can construct a
 *  sibling ``/_thumb/…`` URL rooted at the same node. If the URL doesn't
 *  match the expected shape (e.g. dev-mode absolute URL) we fall back to
 *  a best-effort split at the last ``/``.
 */
function splitProxyBase(url: string): [string, string] {
  const m = /^(.*\/proxy\/[^/]+)\/(.*)$/.exec(url.replace(/\/$/, ""));
  if (m) return [m[1], m[2]];
  const idx = url.lastIndexOf("/");
  return idx > 0 ? [url.slice(0, idx), url.slice(idx + 1)] : [url, ""];
}
