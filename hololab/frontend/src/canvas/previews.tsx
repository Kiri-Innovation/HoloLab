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

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  // The pack's declared viewer spec. Optional because some viewers are
  // tag-driven from the frontend and don't need one (see the tag
  // intercepts at the top of ``Preview()``): a ``frame_sequence`` or
  // ``colmap-cams`` output whose pack manifest is silent about
  // ``preview:`` still routes to the right viewer via its tags. When
  // this is undefined and no tag intercept matches, we render an
  // error pill.
  spec?: OutputPreviewSpec;
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
  // Port tags (from OutputPortSpec.tags). Lets the frontend pick a
  // richer viewer than the tag-registry default when the shape is
  // known — e.g. a ``frame_sequence`` dir gets the stacked-strip
  // viewer instead of the "first frame only" image viewer that the
  // registry falls back to.
  tags?: string[];
  // Effective ``arrayed`` state of the port on this graph node
  // (manifest declaration OR pack.arrayable ∧ node.arrayed_toggle;
  // see ``effectivePortArrayed`` in ``tags.ts``). Preview routing
  // dispatches on ``arrayed<T>`` vs scalar ``T`` for tag families
  // that have a dedicated nested viewer (``arrayed<frame_sequence>``
  // → ``NestedFrameSequencePreview``).
  arrayed?: boolean;
}

export function Preview({
  spec,
  baseUrl,
  storage,
  handleId,
  absolutePath,
  producingNode,
  tags,
  arrayed,
}: PreviewProps) {
  // Tag-driven routing takes precedence over ``spec.viewer``. A
  // ``frame_sequence`` scalar ships from the gateway with
  // ``viewer: "image"`` pointing at the first frame — a legacy default
  // that hides the sequence shape. Its arrayed form goes one level
  // deeper: ``<parent>/<element>/<file>``. Both families have generic
  // per-type viewers so packs don't need to reinvent them.
  if (
    tags &&
    tags.includes("frame_sequence") &&
    storage === "dir"
  ) {
    if (arrayed) {
      return <NestedFrameSequencePreview baseUrl={baseUrl} handleId={handleId} />;
    }
    return <FrameStripPreview baseUrl={baseUrl} />;
  }
  // ``colmap-cams`` — COLMAP sparse reconstruction (cameras.txt +
  // images.txt in a dir handle). Renders inside an iframe backed by
  // the vendored ColmapUtil build (see
  // ``public/colmaputil/HOLOLAB_VENDORED.md``). We route by tag so
  // packs producing this type never need to declare a viewer, matching
  // the frame_sequence pattern above.
  if (
    tags &&
    tags.includes("colmap-cams") &&
    storage === "dir"
  ) {
    return <ColmapCamsPreview baseUrl={baseUrl} />;
  }
  // ``colmap-points`` — triangulated point cloud (points3D.txt in a
  // dir handle). Same iframe/ColmapUtil pipeline as colmap-cams but
  // the reverse file-substitution: real points, empty cameras/images.
  // The arrayed form (one shard per frame from colmap-triangulate[
  // arrayed]) has no dedicated array viewer; ArrayedPaginator wraps
  // the scalar version below with a top pager row.
  if (
    tags &&
    tags.includes("colmap-points") &&
    storage === "dir"
  ) {
    if (arrayed) {
      return (
        <ArrayedPaginator
          baseUrl={baseUrl}
          handleId={handleId}
          renderScalar={(elementBaseUrl) => (
            <ColmapPointsPreview baseUrl={elementBaseUrl} />
          )}
        />
      );
    }
    return <ColmapPointsPreview baseUrl={baseUrl} />;
  }
  // ``colmap`` / ``colmap-frame`` — self-contained COLMAP frame: sparse/0/
  // {cameras,images,points3D}.{bin,txt} + images/ (undistorted). Emitted by
  // ``colmap-triangulate@0.4.0`` as ``colmap`` (arrayed<colmap> under
  // fan-out) and by @0.3.0 as the legacy ``colmap-frame``. Both tags are
  // accepted so historical @0.3.0 handles still preview. The full triple-
  // fetch (cameras+images+points3D) gives genuine frustum wireframes on
  // top of the triangulated point cloud — a richer view than colmap-points
  // (which only has points). The arrayed form wraps the scalar viewer with
  // ArrayedPaginator so the user can page through frames.
  if (
    tags &&
    (tags.includes("colmap") || tags.includes("colmap-frame")) &&
    storage === "dir"
  ) {
    if (arrayed) {
      return (
        <ArrayedPaginator
          baseUrl={baseUrl}
          handleId={handleId}
          renderScalar={(elementBaseUrl) => (
            <ColmapFramePreview baseUrl={elementBaseUrl} />
          )}
        />
      );
    }
    return <ColmapFramePreview baseUrl={baseUrl} />;
  }
  // ``rig_extrinsics`` — rig-calibration ships a COLMAP model at
  // ``sparse_phase2_txt/{cameras,images,points3D}.txt`` (post phase-2
  // BA, converted to text). Reuses Colmap3DPreview to show the 7-cam
  // frustums + triangulated points that the mapper produced.
  if (
    tags &&
    tags.includes("rig_extrinsics") &&
    storage === "dir"
  ) {
    return <RigExtrinsicsPreview baseUrl={baseUrl} />;
  }
  // ``rig_points4d`` — rig-group-triangulation ships one COLMAP model
  // per time-group under ``work/group_NNNN/text/``. The top-level dir
  // isn't an arrayed handle (it's a scalar dir with per-group subdirs),
  // so ArrayedPaginator doesn't fit; this viewer reads
  // ``groups_manifest.json`` for the group list and pages through the
  // per-group models.
  if (
    tags &&
    tags.includes("rig_points4d") &&
    storage === "dir"
  ) {
    return <RigPoints4dPreview baseUrl={baseUrl} />;
  }
  // ``rig_timeline`` — the exposure-timeline diagnostic figure produced
  // by ``rig-temporal-grouping@0.1.1`` (previously briefly emitted by
  // ``rig-frame-extraction@0.1.1``; moved because the bucket edges only
  // become real data once grouping has run). Shows the static PNG with a
  // corner link to the interactive HTML (plotly) for hover tooltips + zoom.
  if (
    tags &&
    tags.includes("rig_timeline") &&
    storage === "dir"
  ) {
    return <RigTimelinePreview baseUrl={baseUrl} />;
  }
  // ``rig_frames`` — per-alias JPEG tree produced by
  // ``rig-frame-extraction`` and passed through by ``rig-temporal-grouping``.
  // Layout: ``<parent>/<alias>/NNNNNN.jpg`` — the classic
  // ``arrayed<frame_sequence>`` shape that ``NestedFrameSequencePreview``
  // handles natively (with element = alias, image = frame).
  if (
    tags &&
    tags.includes("rig_frames") &&
    storage === "dir"
  ) {
    return <NestedFrameSequencePreview baseUrl={baseUrl} handleId={handleId} />;
  }
  // ``rig_capture`` — rig-capture-source's per-alias camera dirs
  // (``camN/<orig-timestamp>.mp4`` + sidecars). Renders a
  // multi-camera video grid on the video-array-source pattern, with
  // one tile per rig camera labelled by its alias.
  if (
    tags &&
    tags.includes("rig_capture") &&
    storage === "dir"
  ) {
    if (!handleId) {
      return (
        <div style={PREVIEW_SHELL}>
          <Status text="rig-capture preview needs a handle id" kind="error" />
        </div>
      );
    }
    return (
      <VideoGridPreview
        baseUrl={baseUrl}
        handleId={handleId}
        // ``filterEntries`` already flattens ``<alias>/<file>``, drops
        // the top-level ``manifest.json``/``rig_map.json``/``set_name.txt``
        // via ``isVideoName``, and drops per-alias ``*.jsonl`` sidecars for
        // the same reason. No explicit glob needed.
        memberGlob={undefined}
        dirAbsolutePath={absolutePath}
        producingNode={producingNode}
        // Alias is the first path segment (``cam0/2026-…mp4`` → ``cam0``).
        entryLabel={(name) => name.split("/", 1)[0] || null}
      />
    );
  }

  // No tag intercept matched and the caller didn't hand us a spec.
  // Happens when a port has a frontend-driven tag (e.g. colmap-cams)
  // for a non-``dir`` storage form the intercepts don't cover, or a
  // future tag was added to FRONTEND_VIEWER_TAGS without a matching
  // intercept clause below. Render an error pill rather than crashing.
  if (!spec) {
    return (
      <div style={PREVIEW_SHELL}>
        <Status
          text={`no viewer registered for tags: ${(tags ?? []).join(", ") || "(none)"}`}
          kind="error"
        />
      </div>
    );
  }

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
// Basic-info panel — the fallback drawer body when the pack has no
// preview viewer for a port but a handle DOES exist. Shows the
// producer's on-disk path, storage kind, total size, and (for a
// dir) a compact listing so an operator can eyeball what the node
// produced without leaving the canvas. Same PREVIEW_SHELL chrome as
// the real viewers so switching drawer contents doesn't reflow.
//
// Rationale: the caret used to be gated on ``spec.preview`` being
// declared — pack authors who hadn't wired a viewer (stg-train,
// regroup-by-frame, colmap-assemble, …) had NO way to see their
// artifact from the canvas. Widening the caret to also open on a
// resolved handle means these nodes need *something* to show; this
// is that something.
// ---------------------------------------------------------------------------

// How many child rows the drawer lists before collapsing into a
// "+N more" tail. Tuned to fit the 340-px expanded slot without
// pushing the drawer taller than a real viewer.
const BASIC_INFO_MAX_ROWS = 8;

export interface BasicInfoPreviewProps {
  handleId: string;
  storage: "dir" | "file";
  absolutePath: string;
  // Falls back to the summary's own ``size_bytes`` when the caller
  // doesn't have one handy; passed in so a freshly-resolved handle
  // (HandleInfo.size_bytes) shows the number without waiting for the
  // summary round trip to complete.
  fallbackSize?: number | null;
}

export function BasicInfoPreview({
  handleId,
  storage,
  absolutePath,
  fallbackSize,
}: BasicInfoPreviewProps) {
  const [state, setState] = useState<
    | { kind: "loading" }
    | { kind: "ok"; summary: HandleSummary }
    | { kind: "err"; message: string }
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

  const rows: Array<{ label: string; value: string; mono?: boolean }> = [];
  rows.push({ label: "path", value: absolutePath, mono: true });
  rows.push({ label: "storage", value: storage });

  if (state.kind === "ok") {
    const s = state.summary;
    const declaredSize = s.size_bytes ?? fallbackSize ?? null;
    if (storage === "file") {
      if (declaredSize !== null) {
        rows.push({ label: "size", value: humanBytes(declaredSize) });
      }
    } else {
      const entries = s.fields.entries ?? [];
      // Prefer the recursive-ish total the summary computed. For an
      // arrayed<T> handle that lives one level deeper, top-level
      // ``total_size_bytes`` is 0 — sum the enriched children so the
      // number isn't misleading.
      let totalBytes = (s.fields.total_size_bytes as number | undefined) ?? 0;
      if (!totalBytes) {
        for (const e of entries) {
          if (e.size_bytes) totalBytes += e.size_bytes;
          for (const c of e.children ?? []) {
            if (c.size_bytes) totalBytes += c.size_bytes;
          }
        }
      }
      if (totalBytes > 0 || declaredSize) {
        rows.push({
          label: "size",
          value: humanBytes(totalBytes || declaredSize),
        });
      }
      const entryCount = (s.fields.entry_count as number | undefined) ?? entries.length;
      rows.push({ label: "entries", value: String(entryCount) });
    }
  } else if (fallbackSize) {
    rows.push({ label: "size", value: humanBytes(fallbackSize) });
  }

  // For dir handles, list the top-level entries. When the layout is
  // arrayed (children present on dir entries) we show the leaf that
  // matters — the video / model / colmap file / … — plus its size so
  // the row is informative on its own.
  const listing = state.kind === "ok" ? summariseListing(state.summary) : null;

  return (
    <div
      data-hl-basic-info=""
      data-hl-storage={storage}
      style={{
        ...PREVIEW_SHELL,
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-2)",
        minHeight: 96,
      }}
    >
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "auto 1fr",
          gap: "4px 12px",
          fontSize: 11,
        }}
      >
        {rows.map((r) => (
          <Fragment key={r.label}>
            <div style={{ color: "var(--inverse-muted)", textAlign: "right" }}>
              {r.label}
            </div>
            <div
              title={r.mono ? r.value : undefined}
              style={{
                color: "var(--text-on-dark)",
                fontFamily: r.mono ? "var(--font-mono)" : "var(--font-sans)",
                wordBreak: r.mono ? "break-all" : "normal",
                userSelect: "text",
              }}
            >
              {r.value}
            </div>
          </Fragment>
        ))}
      </div>
      {state.kind === "loading" && (
        <div style={{ fontSize: 10, color: "var(--inverse-muted)" }}>
          loading…
        </div>
      )}
      {state.kind === "err" && (
        <div style={{ fontSize: 10, color: "var(--error)" }}>
          summary failed: {state.message}
        </div>
      )}
      {listing && listing.rows.length > 0 && (
        <div>
          <div
            style={{
              fontSize: 10,
              color: "var(--inverse-muted)",
              marginBottom: 4,
              fontFamily: "var(--font-mono)",
              letterSpacing: "0.02em",
              textTransform: "uppercase",
            }}
          >
            contents
          </div>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr auto",
              gap: "2px 12px",
              fontSize: 11,
              fontFamily: "var(--font-mono)",
              color: "var(--text-on-dark)",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {listing.rows.map((row) => (
              <Fragment key={row.name}>
                <span
                  title={row.name}
                  style={{
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    minWidth: 0,
                    color: row.isDir
                      ? "var(--inverse-muted)"
                      : "var(--text-on-dark)",
                  }}
                >
                  {row.name}
                  {row.isDir ? "/" : ""}
                </span>
                <span style={{ color: "var(--inverse-muted)", textAlign: "right" }}>
                  {row.sizeLabel}
                </span>
              </Fragment>
            ))}
            {listing.hiddenCount > 0 && (
              <>
                <span style={{ color: "var(--inverse-muted)" }}>
                  … +{listing.hiddenCount} more
                </span>
                <span />
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/** Collapse a directory summary into a compact list of leaf-oriented
 *  rows. For an arrayed layout (top-level all dirs with a single video-
 *  / model-shaped child) we surface the child's name + size — the dir
 *  wrapper adds no information at that level. For a plain flat layout
 *  we list the files directly. Rows are truncated to
 *  BASIC_INFO_MAX_ROWS.
 */
function summariseListing(summary: HandleSummary): {
  rows: Array<{ name: string; sizeLabel: string; isDir: boolean }>;
  hiddenCount: number;
} | null {
  if (summary.storage !== "dir") return null;
  const entries = summary.fields.entries ?? [];
  const rows: Array<{ name: string; sizeLabel: string; isDir: boolean }> = [];
  for (const e of entries) {
    if (e.name.startsWith(".")) continue;
    if (e.is_dir && e.children && e.children.length > 0) {
      // Arrayed-style: surface the (usually single) leaf inside.
      const leaves = e.children.filter((c) => !c.is_dir && !c.name.startsWith("."));
      if (leaves.length === 1) {
        rows.push({
          name: `${e.name}/${leaves[0].name}`,
          sizeLabel: humanBytes(leaves[0].size_bytes),
          isDir: false,
        });
        continue;
      }
      // Fallback for a dir with several files inside — show the dir
      // itself with a count so the row still fits one line.
      // If leaf count is 0 (all children are sub-dirs), show "dir"
      // rather than the misleading "0 files".
      rows.push({
        name: e.name,
        sizeLabel: leaves.length > 0 ? `${leaves.length} files` : "dir",
        isDir: true,
      });
      continue;
    }
    rows.push({
      name: e.name,
      sizeLabel: e.is_dir ? "dir" : humanBytes(e.size_bytes),
      isDir: e.is_dir,
    });
  }
  const shown = rows.slice(0, BASIC_INFO_MAX_ROWS);
  return { rows: shown, hiddenCount: Math.max(0, rows.length - shown.length) };
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
  // Flatten arrayed<video-source> layouts: ``video-array-source`` (and any
  // future arrayed producer) lays elements out as
  // ``<parent>/<element>/<video>``, so the top level is all dirs and the
  // video is one level down. The server enriches directory entries with an
  // immediate ``children`` list (see handle_summary._summarize_dir), so
  // when a top-level entry looks like an arrayed element (dir, no
  // dot-prefix, at least one video-shaped child) we emit one flattened
  // entry per contained video with ``name = "<element>/<video>"``. The
  // URL builders below split on ``/`` and encode each segment
  // independently so the slash survives into the request path.
  const visible: HandleSummaryEntry[] = [];
  for (const e of entries) {
    if (e.name.startsWith(".")) continue;
    if (!e.is_dir) {
      visible.push(e);
      continue;
    }
    if (!e.children) continue;
    for (const c of e.children) {
      if (c.is_dir || c.name.startsWith(".")) continue;
      visible.push({
        name: `${e.name}/${c.name}`,
        is_dir: false,
        size_bytes: c.size_bytes,
      });
    }
  }
  if (glob && glob.trim()) {
    const re = globToRegExp(glob.trim());
    // Match the glob against the leaf name so a manifest that says
    // ``*.mp4`` still works whether the entry is flat or nested.
    return visible.filter((e) => re.test(leafName(e.name)));
  }
  return visible.filter((e) => isVideoName(e.name));
}

function leafName(name: string): string {
  const i = name.lastIndexOf("/");
  return i < 0 ? name : name.slice(i + 1);
}

/** Encode each ``/``-separated segment independently so the slash stays a
 *  path separator in the constructed URL. ``encodeURIComponent`` on the
 *  whole path would turn ``cam00/cam00.mp4`` into ``cam00%2Fcam00.mp4``,
 *  which the node fileserver would reject as a missing file.
 */
function encodePathSegments(path: string): string {
  return path.split("/").map(encodeURIComponent).join("/");
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
  // Optional per-tile label — the returned string is drawn as a small
  // bottom-left pill on top of the video thumbnail. Used by
  // rig-capture-source's preview to surface the alias (``cam0``) up
  // top instead of the raw ``<alias>/<orig-timestamp>.mp4`` string
  // that ends up in the tooltip. Default (undefined / null return) =
  // no label overlay, preserving the video-array-source look.
  entryLabel?: (name: string) => string | null;
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
  entryLabel,
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
              label={entryLabel ? entryLabel(entry.name) : null}
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
  // Optional pill drawn bottom-left. See ``VideoGridProps.entryLabel``.
  label?: string | null;
}

function VideoTile({ baseUrl, entry, isMaster, sync, onZoom, label }: TileProps) {
  // Build the same-origin URLs. ``baseUrl`` is the dir's ``/proxy/{node}/{sub}``.
  // Thumb + preview endpoints are separate node routes rooted at the
  // same ``/proxy/{node}`` prefix.
  const [nodeRoot, dirSub] = useMemo(() => splitProxyBase(baseUrl), [baseUrl]);
  // ``entry.name`` can carry ``/`` when we flattened an arrayed layout
  // (see filterEntries). Encode per segment so the slash survives.
  const encodedEntry = encodePathSegments(entry.name);
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
      {label && (
        <div
          data-hl-tile-label={label}
          style={{
            position: "absolute",
            bottom: 4,
            left: 4,
            padding: "1px 6px",
            background: "rgba(0,0,0,0.55)",
            color: "#fff",
            fontSize: 10,
            fontFamily: "var(--font-mono)",
            fontWeight: 600,
            letterSpacing: "0.02em",
            borderRadius: "var(--radius-sm)",
            pointerEvents: "none",
          }}
        >
          {label}
        </div>
      )}
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
  // Nested (arrayed) entry names carry ``/``; encode per-segment.
  const encodedEntry = encodePathSegments(entry.name);
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

// ---------------------------------------------------------------------------
// FrameStripPreview: dense stack for a ``frame_sequence`` dir.
//
// Layout: N overlapping cards laid out in a single row (like a fanned
// stack of playing cards), followed by a ``count · WxH`` metadata line.
// No scrollbar — the canvas viewport already fights scroll events, and
// the ask was a small at-a-glance affordance, not a paged gallery.
//
// Data path
// ~~~~~~~~~
//   Frame filenames follow the pack contract: ``frames/frame_{i:06d}.png``
//   where ``i`` runs 0..N-1 contiguously. We don't need a directory
//   listing — we probe.
//
//   * Count: exponential-then-binary probe using Range-GET on
//     ``frames/frame_{i:06d}.png`` (bytes=0-0 → 206 exists / 404 absent).
//     Range-GET is used instead of HEAD because the node fileserver's
//     catch-all only registers ``@app.get("/{sub:path}")`` — HEAD
//     returns 404. Also cheaper than a full GET.
//   * First-frame dims: one 32-byte Range-GET, then parse the PNG IHDR.
//   * Thumbnails: reuse the existing ``/_thumb/{W}x{H}/{sub}?at=0``
//     endpoint. ffmpeg accepts PNG input; ``at=0`` is a no-op on stills.
//     Node caches by (path, mtime, dims, at) so revisits are free.
//
// Sampled indices
// ~~~~~~~~~~~~~~~
//   With N total frames and STRIP_MAX_CARDS visible cards, we sample
//   evenly across the sequence rather than showing only the first few
//   — a viewer that always shows frames 0..5 of a 500-frame clip is
//   misleading. The last card sample is always the terminal frame, so
//   "how does the tail look" is answerable at a glance.
// ---------------------------------------------------------------------------

// Cap for the visible cards. Six matches the ~340 px expand-slot width
// at the chosen tile dims + overlap. Bumping this without also
// widening the node would just clip the tail.
const STRIP_MAX_CARDS = 6;

// Card dims (CSS px). 16:9 to mirror the video-grid tile shape and to
// match how source frames usually land (1920×1080, 1280×720, …). The
// server letterboxes non-16:9 sources — same as the grid tile does.
const STRIP_TILE_W = 96;
const STRIP_TILE_H = Math.round((STRIP_TILE_W * 9) / 16); // 54

// Overlap per adjacent card (CSS px). ``STRIP_TILE_W - STRIP_OVERLAP`` is
// how much of each earlier card is still visible under its neighbour.
// 32 px lets six cards fit in ~416 px total, leaving room for the +N
// badge and border.
const STRIP_OVERLAP = 32;

// Server-side thumb dims. 2× the CSS size so retina panels stay crisp;
// still tiny (~5-8 KiB each) versus the 1-3 MiB raw PNG.
const STRIP_THUMB_W = STRIP_TILE_W * 2;
const STRIP_THUMB_H = STRIP_TILE_H * 2;

// Upper bound for the count probe. Frame filenames are 6-digit padded
// (``frame_999999.png`` is the pack's implicit ceiling), so this cap
// mostly exists to bound the worst case when a future pack starts
// dropping many more frames than we've seen.
const STRIP_COUNT_MAX = 200_000;

/** Pad a 0-indexed frame number to the 6-digit form the pack writes. */
function frameName(idx: number): string {
  return `frame_${idx.toString().padStart(6, "0")}.png`;
}

/** Range-GET the first byte of a sub-path — 206 iff it exists.
 *
 *  We use this instead of ``HEAD`` because the node fileserver only
 *  registers the catch-all under ``@app.get(...)`` — HEAD hits FastAPI's
 *  default and comes back 404 for every path. A 1-byte Range GET is
 *  ~free (the server never sends more than the requested byte) and
 *  survives whether or not the intermediate proxy rewrites HEAD.
 */
async function probeExists(url: string, signal: AbortSignal): Promise<boolean> {
  const r = await fetch(url, {
    method: "GET",
    headers: { Range: "bytes=0-0" },
    signal,
  });
  return r.status === 206 || r.ok;
}

/** Find the number of frames by exponential-then-binary probe.
 *
 *  Exponential phase doubles the probe index until we hit an absence
 *  (or the guard cap). Binary phase then narrows to the exact
 *  transition. Worst case for a 50-frame sequence: ~11 requests. For a
 *  10k sequence: ~27 requests. All fetches are 1-byte Range-GETs so
 *  the total bytes moved is negligible.
 *
 *  Assumes the ``frame_XXXXXX.png`` sequence is contiguous from index
 *  0 — that's the pack contract. A hole in the middle would fool this,
 *  but the pack never writes one.
 */
async function probeFrameCount(
  frameUrl: (idx: number) => string,
  signal: AbortSignal,
): Promise<number> {
  // Empty dir guard: if frame 0 is missing we're not looking at a
  // frame_sequence layout at all, so bail with 0.
  if (!(await probeExists(frameUrl(0), signal))) return 0;

  // Exponential phase: 1, 2, 4, 8, … until a probe misses.
  let lo = 0;
  let hi = 1;
  while (hi < STRIP_COUNT_MAX) {
    if (!(await probeExists(frameUrl(hi), signal))) break;
    lo = hi;
    hi = Math.min(hi * 2, STRIP_COUNT_MAX);
  }
  if (hi === STRIP_COUNT_MAX && (await probeExists(frameUrl(hi), signal))) {
    // Sequence exceeded the guard cap — return the cap; the strip will
    // show it as ``≥ STRIP_COUNT_MAX``.
    return STRIP_COUNT_MAX + 1;
  }

  // Binary phase: [lo, hi) with lo known-exists, hi known-absent.
  while (hi - lo > 1) {
    const mid = (lo + hi) >>> 1;
    if (await probeExists(frameUrl(mid), signal)) {
      lo = mid;
    } else {
      hi = mid;
    }
  }
  return lo + 1;
}

/** Fetch the first 32 bytes of a PNG and parse the IHDR width/height. */
async function fetchPngDims(
  url: string,
  signal: AbortSignal,
): Promise<{ width: number; height: number } | null> {
  const r = await fetch(url, {
    method: "GET",
    headers: { Range: "bytes=0-31" },
    signal,
  });
  if (!r.ok && r.status !== 206) return null;
  const buf = new Uint8Array(await r.arrayBuffer());
  if (buf.byteLength < 24) return null;
  const dv = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
  // PNG magic ``89 50 4E 47 0D 0A 1A 0A``.
  if (
    dv.getUint32(0) !== 0x89504e47 ||
    dv.getUint32(4) !== 0x0d0a1a0a
  ) {
    return null;
  }
  // IHDR always begins at offset 16 for a valid PNG (4 length + 4 type
  // + 4 width + 4 height at the start of the chunk data).
  const width = dv.getUint32(16);
  const height = dv.getUint32(20);
  return { width, height };
}

/** Pick ``k`` evenly-spaced indices from ``[0, n-1]`` inclusive at both ends.
 *
 *  Always returns unique, ascending indices. When ``n <= k`` the result
 *  is ``[0..n-1]``.
 */
function sampleIndices(n: number, k: number): number[] {
  if (n <= 0) return [];
  if (n <= k) return Array.from({ length: n }, (_, i) => i);
  const out: number[] = [];
  for (let i = 0; i < k; i += 1) {
    const idx = Math.round((i * (n - 1)) / (k - 1));
    if (out.length === 0 || out[out.length - 1] !== idx) out.push(idx);
  }
  return out;
}

interface FrameStripProps {
  baseUrl: string;
}

function FrameStripPreview({ baseUrl }: FrameStripProps) {
  const [nodeRoot, dirSub] = useMemo(() => splitProxyBase(baseUrl), [baseUrl]);
  const dirBase = baseUrl.replace(/\/$/, "");

  const rawFrameUrl = useCallback(
    (idx: number) => `${dirBase}/frames/${frameName(idx)}`,
    [dirBase],
  );
  const thumbUrl = useCallback(
    (idx: number) =>
      `${nodeRoot}/_thumb/${STRIP_THUMB_W}x${STRIP_THUMB_H}/${dirSub}/frames/${frameName(idx)}?at=0`,
    [nodeRoot, dirSub],
  );

  const [state, setState] = useState<
    | { kind: "loading" }
    | { kind: "ok"; count: number; width: number | null; height: number | null }
    | { kind: "err"; message: string }
  >({ kind: "loading" });

  useEffect(() => {
    const abort = new AbortController();
    (async () => {
      try {
        // Kick both probes in parallel — the count walk hits ~10-30
        // sequential 1-byte GETs, so overlapping the (single) IHDR
        // fetch under it costs us nothing.
        const [count, dims] = await Promise.all([
          probeFrameCount(rawFrameUrl, abort.signal),
          fetchPngDims(rawFrameUrl(0), abort.signal),
        ]);
        setState({
          kind: "ok",
          count,
          width: dims?.width ?? null,
          height: dims?.height ?? null,
        });
      } catch (e) {
        if (abort.signal.aborted) return;
        setState({ kind: "err", message: (e as Error).message });
      }
    })();
    return () => abort.abort();
  }, [rawFrameUrl]);

  const [zoomIdx, setZoomIdx] = useState<number | null>(null);
  useEffect(() => {
    if (zoomIdx === null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setZoomIdx(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [zoomIdx]);

  if (state.kind === "loading") {
    return (
      <div style={PREVIEW_SHELL}>
        <Status text="reading frame sequence…" kind="loading" />
      </div>
    );
  }
  if (state.kind === "err") {
    return (
      <div style={PREVIEW_SHELL}>
        <Status text={`probe failed: ${state.message}`} kind="error" />
      </div>
    );
  }
  if (state.count === 0) {
    return (
      <div style={PREVIEW_SHELL}>
        <Status text="no frames under frames/" kind="info" />
      </div>
    );
  }

  const sampled = sampleIndices(state.count, STRIP_MAX_CARDS);
  const hiddenCount = Math.max(0, state.count - sampled.length);
  const dimsLabel =
    state.width && state.height ? `${state.width}×${state.height}` : null;
  const stripWidth =
    sampled.length === 0
      ? 0
      : STRIP_TILE_W + (sampled.length - 1) * (STRIP_TILE_W - STRIP_OVERLAP);

  return (
    <div
      data-hl-frame-strip=""
      data-hl-frame-count={state.count}
      style={{
        ...PREVIEW_SHELL,
        display: "flex",
        flexDirection: "column",
        gap: 6,
        position: "relative",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          position: "relative",
          height: STRIP_TILE_H + 4,
          width: "100%",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            position: "relative",
            width: stripWidth,
            height: STRIP_TILE_H,
            maxWidth: "100%",
          }}
        >
          {sampled.map((frameIdx, i) => {
            const isLast = i === sampled.length - 1;
            return (
              <button
                type="button"
                key={frameIdx}
                data-hl-frame-card={frameIdx}
                title={`frame ${frameIdx} of ${state.count}`}
                onClick={() => setZoomIdx(frameIdx)}
                className="nodrag nopan"
                style={{
                  position: "absolute",
                  left: i * (STRIP_TILE_W - STRIP_OVERLAP),
                  top: 0,
                  width: STRIP_TILE_W,
                  height: STRIP_TILE_H,
                  padding: 0,
                  background: "#000",
                  border: "1px solid rgba(255,255,255,0.18)",
                  borderRadius: "var(--radius-sm)",
                  overflow: "hidden",
                  cursor: "zoom-in",
                  // Later cards paint on top so the fan reads left→right.
                  zIndex: i + 1,
                  boxShadow: "0 1px 3px rgba(0,0,0,0.35)",
                }}
              >
                <img
                  src={thumbUrl(frameIdx)}
                  alt={`frame ${frameIdx}`}
                  loading="lazy"
                  style={{
                    display: "block",
                    width: "100%",
                    height: "100%",
                    objectFit: "cover",
                    pointerEvents: "none",
                  }}
                />
                {isLast && hiddenCount > 0 && (
                  <div
                    data-hl-frame-more=""
                    style={{
                      position: "absolute",
                      inset: 0,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      background: "rgba(0,0,0,0.55)",
                      color: "#fff",
                      fontSize: 12,
                      fontWeight: 600,
                      fontFamily: "var(--font-mono)",
                      letterSpacing: "0.02em",
                      pointerEvents: "none",
                    }}
                  >
                    +{hiddenCount}
                  </div>
                )}
              </button>
            );
          })}
        </div>
      </div>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          fontSize: 11,
          color: "var(--inverse-muted)",
          fontFamily: "var(--font-mono)",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        <span data-hl-frame-count-label="">
          {state.count > STRIP_COUNT_MAX ? `≥${STRIP_COUNT_MAX}` : state.count} frames
        </span>
        {dimsLabel && (
          <>
            <span aria-hidden style={{ opacity: 0.5 }}>·</span>
            <span>{dimsLabel}</span>
          </>
        )}
      </div>
      {zoomIdx !== null && (
        <FrameZoomOverlay
          url={rawFrameUrl(zoomIdx)}
          index={zoomIdx}
          total={state.count}
          onClose={() => setZoomIdx(null)}
        />
      )}
    </div>
  );
}

function FrameZoomOverlay({
  url,
  index,
  total,
  onClose,
}: {
  url: string;
  index: number;
  total: number;
  onClose: () => void;
}) {
  return (
    <div
      data-hl-frame-zoom=""
      style={{
        position: "absolute",
        inset: 0,
        background: "#000",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 10,
      }}
    >
      <img
        src={url}
        alt={`frame ${index}`}
        style={{
          display: "block",
          maxWidth: "100%",
          maxHeight: "100%",
          objectFit: "contain",
        }}
      />
      <button
        type="button"
        onClick={onClose}
        title="back to strip (Esc)"
        data-hl-frame-zoom-back=""
        className="nodrag nopan"
        style={{
          position: "absolute",
          top: 6,
          left: 6,
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
        }}
      >
        frame {index} / {total - 1}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// NestedFrameSequencePreview: viewer for ``arrayed<frame_sequence>``.
//
// Layout on disk: ``<parent>/<element>/<image>``. Each element is a
// directory of image files; the two current producers are
// ``frame-extraction[arrayed]`` (element = camera, dir = per-frame
// PNGs) and ``regroup-by-frame`` (element = frame, dir = per-camera
// PNGs).  Both are the same type, so both share this viewer.
//
// Main view
// ~~~~~~~~~
//   * Up to ``NESTED_OUTER_CARDS`` outer group cards; each card is a
//     fanned stack of the group's first ``NESTED_INNER_THUMBS``
//     thumbnails + a corner badge showing the group's total image
//     count.
//   * A single line at the bottom-right reads ``共 M 组`` for the total
//     group count.
//   * Click a card → drill down into that group as a
//     ``FrameStripPreview``-style strip (sampled thumbs across the
//     group, zoom-on-click, back button).
//
// Data path
// ~~~~~~~~~
//   Single ``GET /api/handles/{id}/summary``. The server enriches
//   directory entries with immediate ``children``
//   (``handle_summary._list_dir_children``), so we get the per-group
//   image list without any extra listing round trips. Thumbnails go
//   through the node fileserver's ``/_thumb`` endpoint (same route
//   FrameStripPreview + VideoGrid use).
//
// Perf
// ~~~~
//   Main view fires at most ``NESTED_OUTER_CARDS × NESTED_INNER_THUMBS``
//   (= 9) thumbnail GETs. The drill-down fires up to ``STRIP_MAX_CARDS``
//   more. No probes, no full listings — one summary + a handful of tiny
//   image responses.
// ---------------------------------------------------------------------------

interface NestedFrameSequenceProps {
  baseUrl: string;
  handleId?: string;
}

interface NestedGroup {
  name: string;
  imageFiles: string[]; // sorted, image-only file names (may be capped)
  // True count of images in this group even when ``imageFiles`` was
  // truncated by the server's per-drill cap. Server surfaces this as
  // ``entry_count`` on the drilled ``frames/`` subdir (see
  // ``handle_summary._summarize_dir``); falls back to
  // ``imageFiles.length`` when absent (older gateway build).
  totalImageCount: number;
  // Extra path segments between the element dir and each image file.
  // Empty string for the flat ``<element>/<image>`` layout; ``frames/``
  // for the pack-convention ``<element>/frames/<image>`` layout. Always
  // ends with ``/`` when non-empty so URL composition is unconditional.
  pathPrefix: string;
}

const NESTED_OUTER_CARDS = 3;
const NESTED_INNER_THUMBS = 3;

const NESTED_MINI_W = 72;
const NESTED_MINI_H = Math.round((NESTED_MINI_W * 9) / 16); // 41
const NESTED_MINI_OVERLAP = 32;
const NESTED_MINI_THUMB_W = NESTED_MINI_W * 2;
const NESTED_MINI_THUMB_H = NESTED_MINI_H * 2;

const NESTED_IMG_RE = /\.(png|jpe?g|webp|bmp)$/i;

// The node fileserver's ``/_thumb`` route shells out to
// ``ffmpeg -ss 0 -i <file> …``.  On a JPEG that seek lands past the
// single-image stream's zero-duration, ffmpeg encodes nothing, the
// tmp file is never written, and the route 500s — even though the
// raw JPEG served from ``/proxy/.../<file>.jpg`` is fine (rig frames
// are per-alias JPEGs and hit this every time).  Until the backend
// grows an image-input branch that skips ``-ss``, JPEGs bypass the
// thumb pipeline and stream the raw file; other formats keep the
// existing ffmpeg-scaled thumb path so the PNG-based
// frame-extraction[arrayed] preview stays fast.  Callers already
// clip the tile with ``object-fit: cover`` so the intrinsic
// dimensions on the wire don't matter for layout.
function _isJpegName(name: string): boolean {
  return /\.jpe?g$/i.test(name);
}
function nestedTileSrc(
  nodeRoot: string,
  dirSub: string,
  groupName: string,
  pathPrefix: string,
  imgName: string,
  thumbW: number,
  thumbH: number,
): string {
  const memberSub = `${dirSub}/${encodeURIComponent(groupName)}/${pathPrefix}${encodeURIComponent(imgName)}`;
  if (_isJpegName(imgName)) {
    return `${nodeRoot}/${memberSub}`;
  }
  return `${nodeRoot}/_thumb/${thumbW}x${thumbH}/${memberSub}?at=0`;
}

/** Zero-pad-aware compare so ``frame_2`` sorts before ``frame_10``. */
function compareNameNumeric(a: string, b: string): number {
  return a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });
}

function NestedFrameSequencePreview({
  baseUrl,
  handleId,
}: NestedFrameSequenceProps) {
  const [nodeRoot, dirSub] = useMemo(() => splitProxyBase(baseUrl), [baseUrl]);
  const dirBase = baseUrl.replace(/\/$/, "");

  const [state, setState] = useState<
    | { kind: "loading" }
    // ``totalGroupCount`` = count of *all* top-level subdirs in the
    // aggregate handle, whether or not the server had budget left to
    // enrich their ``children`` for a thumbnail. ``groups`` is the
    // subset that came back with at least one image reachable, which
    // may be smaller. Two numbers because the ``共 N 组`` label should
    // reflect the true fan-out count (e.g. 21 cameras), even when the
    // per-frames drill in _summarize_dir ran out of shared budget and
    // couldn't populate thumbs for every element.
    | { kind: "ok"; groups: NestedGroup[]; totalGroupCount: number }
    | { kind: "err"; message: string }
  >({ kind: "loading" });

  useEffect(() => {
    if (!handleId) {
      setState({ kind: "err", message: "handle id required" });
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const summary = await getHandleSummary(handleId);
        if (cancelled) return;
        const entries = summary.fields.entries ?? [];
        const totalGroupCount = entries.filter((e) => e.is_dir).length;
        const groups: NestedGroup[] = entries
          .filter((e) => e.is_dir)
          .sort((a, b) => compareNameNumeric(a.name, b.name))
          .map((e) => {
            // arrayed<frame_sequence> can put images either directly
            // under the element (``<element>/<image>``) or one deeper
            // inside a ``frames/`` subdir (``<element>/frames/<image>``,
            // the current frame-extraction + regroup-by-frame
            // convention). The server enriches both cases into
            // ``children`` / ``children[].children`` so we handle them
            // with the same walker.
            const direct = (e.children ?? []).filter(
              (c) => !c.is_dir && NESTED_IMG_RE.test(c.name),
            );
            let files: string[];
            let pathPrefix: string;
            let totalImageCount: number;
            if (direct.length > 0) {
              files = direct.map((c) => c.name).sort(compareNameNumeric);
              pathPrefix = "";
              // Element-level entry_count includes non-image siblings;
              // fall back to the returned image count when it isn't set.
              totalImageCount = e.entry_count ?? files.length;
            } else {
              const framesDir = (e.children ?? []).find(
                (c) => c.is_dir && c.name === "frames",
              );
              const nested = (framesDir?.children ?? []).filter(
                (c) => !c.is_dir && NESTED_IMG_RE.test(c.name),
              );
              files = nested.map((c) => c.name).sort(compareNameNumeric);
              pathPrefix = framesDir ? "frames/" : "";
              // The drilled ``frames/`` dir only contains images, so its
              // ``entry_count`` is the true frame count even when the
              // ``children`` list was capped at _FRAMES_DRILL_CAP.
              totalImageCount = framesDir?.entry_count ?? files.length;
            }
            return { name: e.name, imageFiles: files, pathPrefix, totalImageCount };
          })
          .filter((g) => g.imageFiles.length > 0);
        setState({ kind: "ok", groups, totalGroupCount });
      } catch (e) {
        if (cancelled) return;
        setState({ kind: "err", message: (e as Error).message });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [handleId]);

  const [detailGroup, setDetailGroup] = useState<string | null>(null);

  if (state.kind === "loading") {
    return (
      <div style={PREVIEW_SHELL}>
        <Status text="reading grouped frames…" kind="loading" />
      </div>
    );
  }
  if (state.kind === "err") {
    return (
      <div style={PREVIEW_SHELL}>
        <Status text={`summary failed: ${state.message}`} kind="error" />
      </div>
    );
  }
  if (state.groups.length === 0) {
    return (
      <div style={PREVIEW_SHELL}>
        <Status text="no groups with images" kind="info" />
      </div>
    );
  }

  const activeGroup =
    detailGroup !== null
      ? (state.groups.find((g) => g.name === detailGroup) ?? null)
      : null;

  if (activeGroup) {
    return (
      <NestedGroupDetail
        group={activeGroup}
        nodeRoot={nodeRoot}
        dirSub={dirSub}
        dirBase={dirBase}
        onBack={() => setDetailGroup(null)}
      />
    );
  }

  const visibleGroups = state.groups.slice(0, NESTED_OUTER_CARDS);
  const hiddenGroups = Math.max(
    0,
    state.totalGroupCount - visibleGroups.length,
  );
  return (
    <div
      data-hl-nested-strip=""
      data-hl-group-count={state.totalGroupCount}
      style={{
        ...PREVIEW_SHELL,
        display: "flex",
        flexDirection: "column",
        gap: 6,
        position: "relative",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          display: "flex",
          gap: 10,
          alignItems: "flex-start",
          flexWrap: "wrap",
        }}
      >
        {visibleGroups.map((g) => (
          <NestedGroupCard
            key={g.name}
            group={g}
            nodeRoot={nodeRoot}
            dirSub={dirSub}
            onClick={() => setDetailGroup(g.name)}
          />
        ))}
      </div>
      <div
        style={{
          display: "flex",
          justifyContent: "flex-end",
          alignItems: "center",
          gap: 8,
          fontSize: 11,
          color: "var(--inverse-muted)",
          fontFamily: "var(--font-mono)",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {hiddenGroups > 0 && (
          <span style={{ opacity: 0.7 }}>+{hiddenGroups} 隐藏</span>
        )}
        <span data-hl-group-count-label="">共 {state.totalGroupCount} 组</span>
      </div>
    </div>
  );
}

function NestedGroupCard({
  group,
  nodeRoot,
  dirSub,
  onClick,
}: {
  group: NestedGroup;
  nodeRoot: string;
  dirSub: string;
  onClick: () => void;
}) {
  const thumbs = group.imageFiles.slice(0, NESTED_INNER_THUMBS);
  const stackWidth =
    thumbs.length === 0
      ? NESTED_MINI_W
      : NESTED_MINI_W +
        (thumbs.length - 1) * (NESTED_MINI_W - NESTED_MINI_OVERLAP);
  return (
    <button
      type="button"
      onClick={onClick}
      title={`${group.name} · ${group.totalImageCount} images (click to expand)`}
      data-hl-group-card={group.name}
      className="nodrag nopan"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 4,
        padding: 0,
        background: "transparent",
        border: "none",
        cursor: "zoom-in",
        color: "inherit",
      }}
    >
      <div
        style={{
          position: "relative",
          width: stackWidth,
          height: NESTED_MINI_H,
        }}
      >
        {thumbs.map((imgName, i) => (
          <div
            key={imgName}
            style={{
              position: "absolute",
              left: i * (NESTED_MINI_W - NESTED_MINI_OVERLAP),
              top: 0,
              width: NESTED_MINI_W,
              height: NESTED_MINI_H,
              background: "#000",
              border: "1px solid rgba(255,255,255,0.18)",
              borderRadius: "var(--radius-sm)",
              overflow: "hidden",
              zIndex: i + 1,
              boxShadow: "0 1px 2px rgba(0,0,0,0.35)",
            }}
          >
            <img
              src={nestedTileSrc(
                nodeRoot,
                dirSub,
                group.name,
                group.pathPrefix,
                imgName,
                NESTED_MINI_THUMB_W,
                NESTED_MINI_THUMB_H,
              )}
              alt={imgName}
              loading="lazy"
              style={{
                display: "block",
                width: "100%",
                height: "100%",
                objectFit: "cover",
                pointerEvents: "none",
              }}
            />
          </div>
        ))}
        <div
          data-hl-group-badge=""
          style={{
            position: "absolute",
            top: 2,
            right: 2,
            padding: "1px 5px",
            background: "rgba(0,0,0,0.7)",
            color: "#fff",
            fontSize: 10,
            fontFamily: "var(--font-mono)",
            fontWeight: 600,
            borderRadius: "var(--radius-pill)",
            zIndex: thumbs.length + 1,
            pointerEvents: "none",
            letterSpacing: "0.02em",
          }}
        >
          {group.totalImageCount}
        </div>
      </div>
      <div
        style={{
          fontSize: 10,
          color: "var(--inverse-muted)",
          fontFamily: "var(--font-mono)",
          fontVariantNumeric: "tabular-nums",
          textAlign: "left",
          maxWidth: stackWidth,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {group.name}
      </div>
    </button>
  );
}

function NestedGroupDetail({
  group,
  nodeRoot,
  dirSub,
  dirBase,
  onBack,
}: {
  group: NestedGroup;
  nodeRoot: string;
  dirSub: string;
  dirBase: string;
  onBack: () => void;
}) {
  const sampledIndices = sampleIndices(group.imageFiles.length, STRIP_MAX_CARDS);
  const sampled = sampledIndices.map((i) => group.imageFiles[i]);
  const hiddenCount = Math.max(0, group.imageFiles.length - sampled.length);
  const stripWidth =
    sampled.length === 0
      ? 0
      : STRIP_TILE_W + (sampled.length - 1) * (STRIP_TILE_W - STRIP_OVERLAP);
  const [zoomIdx, setZoomIdx] = useState<number | null>(null);
  useEffect(() => {
    if (zoomIdx === null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setZoomIdx(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [zoomIdx]);
  const zoomedName = zoomIdx !== null ? group.imageFiles[zoomIdx] : null;
  return (
    <div
      data-hl-group-detail={group.name}
      style={{
        ...PREVIEW_SHELL,
        display: "flex",
        flexDirection: "column",
        gap: 6,
        position: "relative",
        overflow: "hidden",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <button
          type="button"
          onClick={onBack}
          className="nodrag nopan"
          title="back to groups"
          data-hl-group-back=""
          style={{
            padding: "2px 8px",
            background: "transparent",
            color: "var(--inverse-muted)",
            border: "1px solid rgba(255,255,255,0.24)",
            borderRadius: "var(--radius-pill)",
            fontSize: 10,
            fontFamily: "var(--font-sans)",
            cursor: "pointer",
            lineHeight: 1,
          }}
        >
          ← 返回组
        </button>
        <span
          style={{
            fontSize: 11,
            color: "var(--text-on-dark)",
            fontFamily: "var(--font-mono)",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {group.name}
        </span>
      </div>
      <div
        style={{
          position: "relative",
          height: STRIP_TILE_H + 4,
          width: "100%",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            position: "relative",
            width: stripWidth,
            height: STRIP_TILE_H,
            maxWidth: "100%",
          }}
        >
          {sampled.map((imgName, i) => {
            const isLast = i === sampled.length - 1;
            const imgIdx = sampledIndices[i];
            return (
              <button
                type="button"
                key={imgName}
                onClick={() => setZoomIdx(imgIdx)}
                data-hl-frame-card={imgIdx}
                title={`${imgName} (${imgIdx + 1} of ${group.imageFiles.length})`}
                className="nodrag nopan"
                style={{
                  position: "absolute",
                  left: i * (STRIP_TILE_W - STRIP_OVERLAP),
                  top: 0,
                  width: STRIP_TILE_W,
                  height: STRIP_TILE_H,
                  padding: 0,
                  background: "#000",
                  border: "1px solid rgba(255,255,255,0.18)",
                  borderRadius: "var(--radius-sm)",
                  overflow: "hidden",
                  cursor: "zoom-in",
                  zIndex: i + 1,
                  boxShadow: "0 1px 3px rgba(0,0,0,0.35)",
                }}
              >
                <img
                  src={nestedTileSrc(
                    nodeRoot,
                    dirSub,
                    group.name,
                    group.pathPrefix,
                    imgName,
                    STRIP_THUMB_W,
                    STRIP_THUMB_H,
                  )}
                  alt={imgName}
                  loading="lazy"
                  style={{
                    display: "block",
                    width: "100%",
                    height: "100%",
                    objectFit: "cover",
                    pointerEvents: "none",
                  }}
                />
                {isLast && hiddenCount > 0 && (
                  <div
                    style={{
                      position: "absolute",
                      inset: 0,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      background: "rgba(0,0,0,0.55)",
                      color: "#fff",
                      fontSize: 12,
                      fontWeight: 600,
                      fontFamily: "var(--font-mono)",
                      letterSpacing: "0.02em",
                      pointerEvents: "none",
                    }}
                  >
                    +{hiddenCount}
                  </div>
                )}
              </button>
            );
          })}
        </div>
      </div>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          fontSize: 11,
          color: "var(--inverse-muted)",
          fontFamily: "var(--font-mono)",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        <span>{group.imageFiles.length} images</span>
      </div>
      {zoomIdx !== null && zoomedName && (
        <FrameZoomOverlay
          url={`${dirBase}/${encodeURIComponent(group.name)}/${group.pathPrefix}${encodeURIComponent(zoomedName)}`}
          index={zoomIdx}
          total={group.imageFiles.length}
          onClose={() => setZoomIdx(null)}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Colmap3DPreview — 3D COLMAP viewer for ``colmap-*`` dir handles.
//
// Rendered as ``<iframe src="/colmaputil/index.html?embed=1">`` pointing
// at the vendored ColmapUtil build under ``public/colmaputil/``. Data flow:
//
//   1. iframe boots, posts ``{type: 'colmap-ready'}`` to parent.
//   2. parent fetches whichever of ``cameras.txt`` / ``images.txt`` /
//      ``points3D.txt`` the ``fetchFiles`` prop names from the proxy
//      base URL (already same-origin via the /proxy mount).
//   3. parent posts ``{type: 'colmap-load-files', files: [...]}``
//      substituting an empty-header scaffold for any of the three that
//      isn't in ``fetchFiles`` (ColmapUtil's loader mandates all three
//      files, but happily renders "0 of X" when one side is empty).
//
// Two producing tags use it via wrapper components:
//   * ``colmap-cams`` — SfM poses only (cameras.txt+images.txt real,
//     points empty) → all cameras, 0 points.
//   * ``colmap-points`` — triangulated points only (points3D.txt real,
//     cameras/images empty) → 0 cameras, N points as a naked cloud.
//
// See ``docs/pack-spec.md#Previews`` for the architecture rationale
// and ``public/colmaputil/HOLOLAB_VENDORED.md`` for the refresh
// workflow when ColmapUtil ships new features.
// ---------------------------------------------------------------------------

const COLMAP_IFRAME_HEIGHT = 260;

// Empty scaffolds — model_converter --output_type TXT emits headers of
// this shape for a reconstruction with zero of the given entity. Kept
// as constants so the byte pattern is stable across preview mounts.
const COLMAP_EMPTY_CAMERAS =
  "# Camera list with one line of data per camera:\n" +
  "#   CAMERA_ID, MODEL, WIDTH, HEIGHT, PARAMS[]\n" +
  "# Number of cameras: 0\n";
const COLMAP_EMPTY_IMAGES =
  "# Image list with two lines of data per image:\n" +
  "#   IMAGE_ID, QW, QX, QY, QZ, TX, TY, TZ, CAMERA_ID, NAME\n" +
  "#   POINTS2D[] as (X, Y, POINT3D_ID)\n" +
  "# Number of images: 0, mean observations per image: 0\n";
const COLMAP_EMPTY_POINTS3D =
  "# 3D point list with one line of data per point:\n" +
  "#   POINT3D_ID, X, Y, Z, R, G, B, ERROR, TRACK[] as (IMAGE_ID, POINT2D_IDX)\n" +
  "# Number of points: 0, mean track length: 0\n";

type ColmapFileName = "cameras.txt" | "images.txt" | "points3D.txt";

const COLMAP_EMPTY: Record<ColmapFileName, string> = {
  "cameras.txt": COLMAP_EMPTY_CAMERAS,
  "images.txt": COLMAP_EMPTY_IMAGES,
  "points3D.txt": COLMAP_EMPTY_POINTS3D,
};

interface Colmap3DPreviewProps {
  baseUrl: string;
  // Subset of {cameras.txt, images.txt, points3D.txt} the caller wants
  // fetched from ``baseUrl``. The remainder is substituted with the
  // empty-header scaffold so ColmapUtil's loader still gets all three.
  fetchFiles: readonly ColmapFileName[];
  title: string;
}

function Colmap3DPreview({ baseUrl, fetchFiles, title }: Colmap3DPreviewProps) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const sentRef = useRef(false);
  const [error, setError] = useState<string | null>(null);
  const [handshaken, setHandshaken] = useState(false);
  const [activated, setActivated] = useState(false);

  // Deactivate on click-outside or Esc — always live, setActivated(false)
  // is a no-op when already inactive so there's no conditional guard.
  //
  // Must use capture phase (true) because xyflow nodes use d3-drag which calls
  // stopImmediatePropagation() on mousedown, killing all bubble-phase listeners
  // before they reach the document. Capture fires top-down before d3-drag's
  // handler, so it cannot be suppressed. Only nodrag elements bypass d3-drag
  // (that's why the drawer blank space worked but canvas/other nodes didn't).
  useEffect(() => {
    function onDown(e: MouseEvent) {
      if (!wrapperRef.current || wrapperRef.current.contains(e.target as Node))
        return;
      setActivated(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setActivated(false);
    }
    document.addEventListener("mousedown", onDown, true);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown, true);
      document.removeEventListener("keydown", onKey);
    };
  }, []);

  const fetchKey = fetchFiles.join(",");
  useEffect(() => {
    sentRef.current = false;
    setError(null);
    setHandshaken(false);
    setActivated(false);

    const ctl = new AbortController();

    async function sendSparse(target: Window) {
      if (sentRef.current) return;
      const dirBase = baseUrl.replace(/\/$/, "");
      try {
        const fetchedBlobs = await Promise.all(
          fetchFiles.map(async (name) => {
            const r = await fetch(`${dirBase}/${name}`, { signal: ctl.signal });
            if (!r.ok) throw new Error(`${name}: HTTP ${r.status}`);
            return [name, await r.blob()] as const;
          }),
        );
        if (ctl.signal.aborted) return;
        const fetched = new Map(fetchedBlobs);
        const files = (["cameras.txt", "images.txt", "points3D.txt"] as const).map(
          (name) => ({
            name,
            blob:
              fetched.get(name) ??
              new Blob([COLMAP_EMPTY[name]], { type: "text/plain" }),
          }),
        );
        target.postMessage(
          { type: "colmap-load-files", files, name: "sfm" },
          "*",
        );
        sentRef.current = true;
      } catch (e) {
        if ((e as Error).name === "AbortError") return;
        setError((e as Error).message);
      }
    }

    function onMessage(e: MessageEvent) {
      const iframe = iframeRef.current;
      if (!iframe || e.source !== iframe.contentWindow) return;
      const d = e.data as { type?: string } | null;
      if (!d || typeof d !== "object") return;
      if (d.type === "colmap-ready") {
        setHandshaken(true);
        sendSparse(iframe.contentWindow as Window);
      }
    }
    window.addEventListener("message", onMessage);
    return () => {
      ctl.abort();
      window.removeEventListener("message", onMessage);
    };
  }, [baseUrl, fetchKey]);

  return (
    <div ref={wrapperRef} style={{ position: "relative" }}>
      <iframe
        ref={iframeRef}
        // ``?embed=1`` hides ColmapUtil's InitiationPage / sidebar /
        // footer, leaving only the 3D visualizer. It also arms the
        // EmbedDataListener which broadcasts ``colmap-ready`` on
        // mount so we know when it's safe to postMessage.
        src="/colmaputil/index.html?embed=1"
        title={title}
        // allow-scripts: viewer needs JS. allow-same-origin: served
        // from same origin (vite proxy in dev, SPA mount in prod) so
        // its own asset chunks resolve without a cross-origin dance.
        sandbox="allow-scripts allow-same-origin"
        style={{
          width: "100%",
          height: COLMAP_IFRAME_HEIGHT,
          border: activated
            ? "1.5px solid var(--accent)"
            : "1px solid var(--border-strong)",
          borderRadius: "var(--radius-sm)",
          background: "#000",
          display: "block",
        }}
      />
      {(!handshaken || error) && (
        <div
          style={{
            position: "absolute",
            inset: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            background: "rgba(0,0,0,0.55)",
            color: error ? "var(--status-failed)" : "var(--text-on-dark)",
            fontSize: 11,
            pointerEvents: "none",
            padding: "0 var(--space-2)",
            textAlign: "center",
          }}
        >
          {error ? `load failed — ${error}` : "loading viewer…"}
        </div>
      )}
      {/* Transparent shield — blocks wheel/hover passthrough when inactive.
          Sits above the loading overlay (z:10 vs nothing) so the first
          click on a loaded viewer activates rather than firing into the iframe. */}
      {handshaken && !error && !activated && (
        <div
          onClick={() => setActivated(true)}
          style={{
            position: "absolute",
            inset: 0,
            zIndex: 10,
            cursor: "default",
            background: "transparent",
          }}
        />
      )}
    </div>
  );
}

const COLMAP_CAMS_FETCH = ["cameras.txt", "images.txt"] as const;
const COLMAP_POINTS_FETCH = ["points3D.txt"] as const;
const COLMAP_FRAME_FETCH = ["cameras.txt", "images.txt", "points3D.txt"] as const;

function ColmapCamsPreview({ baseUrl }: { baseUrl: string }) {
  return (
    <Colmap3DPreview
      baseUrl={baseUrl}
      fetchFiles={COLMAP_CAMS_FETCH}
      title="colmap-cams viewer"
    />
  );
}

function ColmapPointsPreview({ baseUrl }: { baseUrl: string }) {
  return (
    <Colmap3DPreview
      baseUrl={baseUrl}
      fetchFiles={COLMAP_POINTS_FETCH}
      title="colmap-points viewer"
    />
  );
}

function ColmapFramePreview({ baseUrl }: { baseUrl: string }) {
  // triangulate @0.3.0 / @0.4.0 store the COLMAP model under sparse/0/
  // inside each frame element directory — adjust the base before the
  // standard triple-fetch. Naming kept as ``ColmapFramePreview`` for
  // historical parity even though @0.4.0 emits the ``colmap`` tag.
  const sparseBase = `${baseUrl.replace(/\/$/, "")}/sparse/0`;
  return (
    <Colmap3DPreview
      baseUrl={sparseBase}
      fetchFiles={COLMAP_FRAME_FETCH}
      title="colmap frame viewer"
    />
  );
}

// ---------------------------------------------------------------------------
// Rig previews — thin wrappers over Colmap3DPreview for outputs shaped by
// the rig calibration / triangulation packs.
// ---------------------------------------------------------------------------

// rig-calibration puts its COLMAP model at ``sparse_phase2_txt/`` after
// the phase-2 bundle_adjuster + model_converter. Adjust the base URL
// once and hand the standard triple-fetch to Colmap3DPreview.
function RigExtrinsicsPreview({ baseUrl }: { baseUrl: string }) {
  const sparseBase = `${baseUrl.replace(/\/$/, "")}/sparse_phase2_txt`;
  return (
    <Colmap3DPreview
      baseUrl={sparseBase}
      fetchFiles={COLMAP_FRAME_FETCH}
      title="rig-calibration viewer"
    />
  );
}

// rig-group-triangulation ships ``groups_manifest.json`` listing every
// group + its ``work/group_NNNN/text/`` COLMAP model. A group is only
// meaningful together with its timestamp/point count so the pager row
// shows both; ``failed`` groups from the manifest are dropped since they
// have no model to render.
interface RigGroupMeta {
  index: number;
  t_center_ns?: number;
  registered_images?: number;
  points3D?: number;
}
interface RigGroupsManifest {
  groups?: RigGroupMeta[];
  failed?: number[];
}

function RigPoints4dPreview({ baseUrl }: { baseUrl: string }) {
  const [state, setState] = useState<
    | { kind: "loading" }
    | { kind: "ok"; groups: RigGroupMeta[] }
    | { kind: "err"; message: string }
  >({ kind: "loading" });
  const [idx, setIdx] = useState(0);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const [hovered, setHovered] = useState(false);

  useEffect(() => {
    const ctl = new AbortController();
    setState({ kind: "loading" });
    setIdx(0);
    (async () => {
      try {
        const dirBase = baseUrl.replace(/\/$/, "");
        const r = await fetch(`${dirBase}/groups_manifest.json`, {
          signal: ctl.signal,
        });
        if (!r.ok) throw new Error(`groups_manifest.json: HTTP ${r.status}`);
        const parsed = (await r.json()) as RigGroupsManifest;
        const groups = (parsed.groups ?? []).filter(
          (g) => typeof g.index === "number",
        );
        if (ctl.signal.aborted) return;
        setState({ kind: "ok", groups });
      } catch (e) {
        if ((e as Error).name === "AbortError") return;
        setState({ kind: "err", message: (e as Error).message });
      }
    })();
    return () => ctl.abort();
  }, [baseUrl]);

  const total = state.kind === "ok" ? state.groups.length : 0;
  const move = useCallback(
    (delta: number) => {
      if (total <= 0) return;
      setIdx((prev) => (prev + delta + total) % total);
    },
    [total],
  );

  useEffect(() => {
    if (!hovered) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "ArrowLeft") {
        move(-1);
        e.preventDefault();
      } else if (e.key === "ArrowRight") {
        move(1);
        e.preventDefault();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [hovered, move]);

  if (state.kind === "loading") {
    return (
      <div style={PREVIEW_SHELL}>
        <Status text="reading groups_manifest.json…" kind="loading" />
      </div>
    );
  }
  if (state.kind === "err") {
    return (
      <div style={PREVIEW_SHELL}>
        <Status text={`groups load failed: ${state.message}`} kind="error" />
      </div>
    );
  }
  if (state.groups.length === 0) {
    return (
      <div style={PREVIEW_SHELL}>
        <Status text="no groups" kind="info" />
      </div>
    );
  }

  const safeIdx = Math.min(idx, state.groups.length - 1);
  const group = state.groups[safeIdx];
  const groupTag = `group_${String(group.index).padStart(4, "0")}`;
  const groupBase = `${baseUrl.replace(/\/$/, "")}/work/${groupTag}/text`;
  const points = group.points3D ?? 0;
  const cams = group.registered_images ?? 0;
  const detail = `${cams} cams · ${points} pts`;

  return (
    <div
      ref={wrapperRef}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          gap: 8,
          padding: "0 0 var(--space-1) 0",
          fontSize: 11,
          color: "var(--inverse-muted)",
          fontFamily: "var(--font-mono)",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        <button
          type="button"
          onClick={() => move(-1)}
          className="nodrag nopan"
          style={{
            background: "transparent",
            border: "1px solid var(--border-strong)",
            color: "var(--text-on-dark)",
            padding: "1px 6px",
            borderRadius: "var(--radius-sm)",
            cursor: "pointer",
            fontFamily: "inherit",
          }}
          aria-label="previous group"
        >
          ‹
        </button>
        <span>
          {groupTag} ({safeIdx + 1}/{state.groups.length}) · {detail}
        </span>
        <button
          type="button"
          onClick={() => move(1)}
          className="nodrag nopan"
          style={{
            background: "transparent",
            border: "1px solid var(--border-strong)",
            color: "var(--text-on-dark)",
            padding: "1px 6px",
            borderRadius: "var(--radius-sm)",
            cursor: "pointer",
            fontFamily: "inherit",
          }}
          aria-label="next group"
        >
          ›
        </button>
      </div>
      <Colmap3DPreview
        baseUrl={groupBase}
        fetchFiles={COLMAP_FRAME_FETCH}
        title="rig-points4d viewer"
      />
    </div>
  );
}

// rig-frame-extraction@0.1.1 ships the exposure-timeline PNG/HTML pair as
// a soft second output. The preview is the static ``_full`` PNG (never
// obstructive, always renders inside the node card); the interactive
// plotly HTML is one click away via the corner link — new tab so the
// canvas doesn't get scrolled/refreshed.
function RigTimelinePreview({ baseUrl }: { baseUrl: string }) {
  const dir = baseUrl.replace(/\/$/, "");
  const pngUrl = `${dir}/exposure_timeline_full.png`;
  const htmlUrl = `${dir}/exposure_timeline_zoom.html`;
  return (
    <div style={{ ...PREVIEW_SHELL, position: "relative" }}>
      <img
        src={pngUrl}
        alt="exposure timeline (full public overlap window)"
        style={{
          display: "block",
          margin: "0 auto",
          maxWidth: "100%",
          maxHeight: 260,
          borderRadius: "var(--radius-sm)",
        }}
      />
      <a
        href={htmlUrl}
        target="_blank"
        rel="noreferrer noopener"
        className="nodrag nopan"
        title="open interactive zoom timeline (plotly HTML) in a new tab"
        style={{
          position: "absolute",
          top: 6,
          right: 8,
          background: "rgba(0,0,0,0.55)",
          color: "var(--text-on-dark)",
          textDecoration: "none",
          fontSize: 10,
          fontFamily: "var(--font-mono)",
          padding: "2px 6px",
          borderRadius: "var(--radius-sm)",
          border: "1px solid var(--border-strong)",
        }}
      >
        interactive ↗
      </a>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ArrayedPaginator — top pager row + scalar viewer of the selected element.
//
// The default frontend policy is "predefine a scalar viewer for tag T; if
// no arrayed<T> viewer exists, wrap the scalar one with this paginator."
// See ``docs/pack-spec.md#Previews`` — this is the generic escape hatch so
// arrayed outputs are never left previewless.
//
// Data flow
// ~~~~~~~~~
//   * Single ``GET /api/handles/{id}/summary`` gives the top-level entries;
//     each ``is_dir`` entry is one array element. Sorted zero-pad-aware
//     (``frame_9`` < ``frame_10``) so the pager order matches on-disk.
//   * The selected element's ``baseUrl`` is composed as ``<baseUrl>/<name>``;
//     the scalar viewer sees exactly the URL it would see for a scalar
//     handle pointing at that subdir.
//
// UI
// ~~
//   * ‹ prev · current name (i / N) · next ›  — tokens + double-theme
//     compliant. Compact enough to sit above a 260-px iframe without
//     eating vertical room.
//   * ArrowLeft / ArrowRight while hovered advance the pager.
//   * Mounting a fresh scalar with a new baseUrl re-runs its data effect,
//     which is fine — the iframe stays put and just receives new files.
// ---------------------------------------------------------------------------

interface ArrayedPaginatorProps {
  baseUrl: string;
  handleId?: string;
  renderScalar: (elementBaseUrl: string) => React.ReactNode;
}

function ArrayedPaginator({
  baseUrl,
  handleId,
  renderScalar,
}: ArrayedPaginatorProps) {
  const [state, setState] = useState<
    | { kind: "loading" }
    | { kind: "ok"; elements: string[] }
    | { kind: "err"; message: string }
  >({ kind: "loading" });
  const [idx, setIdx] = useState(0);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const [hovered, setHovered] = useState(false);

  useEffect(() => {
    if (!handleId) {
      setState({ kind: "err", message: "handle id required" });
      return;
    }
    let cancelled = false;
    setState({ kind: "loading" });
    setIdx(0);
    (async () => {
      try {
        const summary = await getHandleSummary(handleId);
        if (cancelled) return;
        const entries = summary.fields.entries ?? [];
        const names = entries
          .filter((e) => e.is_dir && !e.name.startsWith("."))
          .map((e) => e.name)
          .sort(compareNameNumeric);
        setState({ kind: "ok", elements: names });
      } catch (e) {
        if (cancelled) return;
        setState({ kind: "err", message: (e as Error).message });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [handleId]);

  const total = state.kind === "ok" ? state.elements.length : 0;
  const move = useCallback(
    (delta: number) => {
      if (total <= 0) return;
      setIdx((prev) => (prev + delta + total) % total);
    },
    [total],
  );

  useEffect(() => {
    if (!hovered) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "ArrowLeft") {
        move(-1);
        e.preventDefault();
      } else if (e.key === "ArrowRight") {
        move(1);
        e.preventDefault();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [hovered, move]);

  if (state.kind === "loading") {
    return (
      <div style={PREVIEW_SHELL}>
        <Status text="reading elements…" kind="loading" />
      </div>
    );
  }
  if (state.kind === "err") {
    return (
      <div style={PREVIEW_SHELL}>
        <Status text={`summary failed: ${state.message}`} kind="error" />
      </div>
    );
  }
  if (state.elements.length === 0) {
    return (
      <div style={PREVIEW_SHELL}>
        <Status text="no elements" kind="info" />
      </div>
    );
  }

  const safeIdx = Math.min(idx, state.elements.length - 1);
  const currentName = state.elements[safeIdx];
  const dirBase = baseUrl.replace(/\/$/, "");
  const elementUrl = `${dirBase}/${encodePathSegments(currentName)}`;
  const isSingle = state.elements.length === 1;

  return (
    <div
      ref={wrapperRef}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      data-hl-arrayed-paginator=""
      data-hl-arrayed-count={state.elements.length}
      style={{
        ...PREVIEW_SHELL,
        display: "flex",
        flexDirection: "column",
        gap: 6,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 8,
          fontSize: 11,
          fontFamily: "var(--font-mono)",
          fontVariantNumeric: "tabular-nums",
          color: "var(--text-on-dark)",
        }}
      >
        <button
          type="button"
          onClick={() => move(-1)}
          disabled={isSingle}
          aria-label="previous element"
          className="nodrag nopan"
          style={pagerBtnStyle(isSingle)}
        >
          ‹
        </button>
        <div
          title={currentName}
          data-hl-arrayed-current={currentName}
          style={{
            flex: 1,
            minWidth: 0,
            display: "flex",
            justifyContent: "center",
            alignItems: "baseline",
            gap: 8,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          <span
            style={{
              overflow: "hidden",
              textOverflow: "ellipsis",
              minWidth: 0,
            }}
          >
            {currentName}
          </span>
          <span style={{ color: "var(--inverse-muted)" }}>
            {safeIdx + 1} / {state.elements.length}
          </span>
        </div>
        <button
          type="button"
          onClick={() => move(1)}
          disabled={isSingle}
          aria-label="next element"
          className="nodrag nopan"
          style={pagerBtnStyle(isSingle)}
        >
          ›
        </button>
      </div>
      {/* Keying by element name so React remounts the scalar viewer on
          selection change — cheap, and avoids stale-fetch races inside
          the wrapped viewer's own useEffect. */}
      <div key={currentName}>{renderScalar(elementUrl)}</div>
    </div>
  );
}

function pagerBtnStyle(disabled: boolean): React.CSSProperties {
  return {
    appearance: "none",
    border: "1px solid var(--border-strong)",
    background: "transparent",
    color: disabled ? "var(--inverse-muted)" : "var(--text-on-dark)",
    borderRadius: "var(--radius-sm)",
    width: 22,
    height: 20,
    lineHeight: "18px",
    padding: 0,
    fontFamily: "inherit",
    fontSize: 13,
    cursor: disabled ? "default" : "pointer",
    opacity: disabled ? 0.4 : 1,
  };
}
