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
import type { HandleSummary, HandleSummaryEntry, OutputPreviewSpec } from "../wire";

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
}

export function Preview({ spec, baseUrl, storage, handleId }: PreviewProps) {
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
      return <VideoGridPreview baseUrl={baseUrl} handleId={handleId} memberGlob={spec.member} />;
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

  const togglePlay = useCallback(() => setPlaying((p) => !p), []);

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

  const duration = useMemo(() => {
    const vals = Object.values(durations);
    return vals.length === 0 ? 0 : Math.max(...vals);
  }, [durations]);

  return {
    playing,
    currentTime,
    duration,
    register,
    reportDuration,
    reportMasterTime,
    togglePlay,
    seek,
  };
}

function VideoGridPreview({ baseUrl, handleId, memberGlob }: VideoGridProps) {
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
}

function ZoomOverlay({ baseUrl, entry, sync, onClose }: ZoomOverlayProps) {
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
          padding: "2px 8px",
          background: "rgba(0,0,0,0.55)",
          color: "#fff",
          border: "1px solid rgba(255,255,255,0.18)",
          borderRadius: "var(--radius-pill)",
          fontSize: 10,
          fontFamily: "var(--font-mono)",
          maxWidth: "60%",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
        title={entry.name}
      >
        {entry.name}
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
  // ``nodrag`` / ``nopan`` are xyflow class-name hooks. When present
  // anywhere on a pointer-target's ancestry, ReactFlow skips its
  // node-drag / pane-pan handlers respectively for that gesture.
  // Without them, dragging the scrubber gets intercepted as a
  // node move because the whole node is a ReactFlow drag surface.
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
        title={sync.playing ? "pause all" : "play all"}
        data-hl-play=""
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
        {sync.playing ? "❚❚" : "▶"}
      </button>
      <input
        type="range"
        min={0}
        max={Math.max(sync.duration, 0.01)}
        step={0.01}
        value={Math.min(sync.currentTime, sync.duration || 0)}
        disabled={disabled}
        onChange={(e) => sync.seek(Number.parseFloat(e.target.value))}
        data-hl-scrub=""
        className="nodrag nopan"
        // Belt + braces on top of ``nodrag`` — stop pointerdown so
        // ReactFlow's document-level listener doesn't grab the drag
        // gesture on browsers where the class-hook alone isn't enough
        // (older xyflow versions, or when a wrapper reads it late).
        onPointerDown={(e) => e.stopPropagation()}
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
