// "Jump to source" — canvas-header code-icon button. Same Cobrowser
// integration as the preview drawer's ↗ button, but the target file is
// the pack's implementation script, not a produced artifact:
//
//   * If the pack manifest declared ``source_entry`` (a path relative
//     to the Kiri4DGS workspace root, or absolute), that file is
//     opened. Sharp-4DGS / SpacetimeGaussians / Utils packs use this
//     to point straight at ``sharp-4dgs/per-frame/video_to_colmap.py``,
//     ``SpacetimeGaussians/train.py`` and friends.
//   * Otherwise the button falls back to
//     ``{packs_dir}/{name}@{version}/manifest.yaml`` — the exec
//     orchestration lives there; it's always present.
//
// The compute node is picked by preference: the graph node's explicit
// assignment, else the first online node offering this pack. Without
// a compute node the button still renders (per the design: guide the
// operator to configure things) but flops_executor_id resolution and
// ``packs_dir`` may be missing — in that case we fall back to opening
// what we can and let the ``not-found`` reason surface from the API.
//
// See docs/cobrowser-integration.md#jump-to-source.

import { useEffect, useState } from "react";
import type { CatalogPack, ComputeNode } from "../wire";
import { flopsAvailable, flopsShowDocument } from "../flops";
import { FlopsExecutorGuide } from "./FlopsExecutorGuide";

export interface OpenSourceButtonProps {
  pack: CatalogPack;
  /** The compute node whose ``flops_executor_id`` + ``packs_dir`` we
   *  resolve against. Null when no online node offers this pack right
   *  now — the button still renders so the operator can see it, but
   *  clicking shows the guide callout (there's nowhere to open the
   *  file on). */
  computeNode: ComputeNode | null;
  onDark?: boolean;
}

type Status =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ok" }
  | { kind: "err"; msg: string };

/** Compact 12×12 ``</>`` glyph — vector code icon in the lucide style. */
function CodeGlyph({ colour }: { colour: string }) {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 24 24"
      fill="none"
      stroke={colour}
      strokeWidth="2.2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <polyline points="16 18 22 12 16 6" />
      <polyline points="8 6 2 12 8 18" />
    </svg>
  );
}

/** Resolve the target absolute path Cocoder will open.
 *
 *  Semantics documented on ``Manifest.source_entry`` and mirrored in
 *  docs/cobrowser-integration.md#jump-to-source. Exported for test.
 */
export function resolveSourceTarget(
  pack: CatalogPack,
  packsDir: string | null,
): string | null {
  const entry = pack.source_entry;
  if (entry && entry.startsWith("/")) {
    // Absolute path — respected verbatim; ignores packs_dir.
    return entry;
  }
  if (!packsDir) {
    // We can't resolve either the manifest.yaml fallback OR a relative
    // source_entry without a packs_dir. Bail — caller renders as
    // unconfigured (fires the guide callout when clicked).
    return null;
  }
  const trimmed = packsDir.replace(/\/+$/, "");
  if (entry) {
    // Relative to Kiri4DGS repo root (packs_dir.parent.parent per the
    // spec). Compute that from packs_dir by stripping two components.
    const parts = trimmed.split("/");
    if (parts.length < 3) return null; // pathological packs_dir
    const codeRoot = parts.slice(0, -2).join("/");
    return `${codeRoot}/${entry}`;
  }
  return `${trimmed}/${pack.name}@${pack.version}/manifest.yaml`;
}

export function OpenSourceButton({
  pack,
  computeNode,
  onDark = false,
}: OpenSourceButtonProps) {
  const [available, setAvailable] = useState<boolean>(() => flopsAvailable());
  useEffect(() => {
    if (!available && flopsAvailable()) setAvailable(true);
  }, [available]);

  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const [showGuide, setShowGuide] = useState<boolean>(false);

  useEffect(() => {
    if (status.kind === "ok" || status.kind === "err") {
      const t = window.setTimeout(() => setStatus({ kind: "idle" }), 3500);
      return () => window.clearTimeout(t);
    }
  }, [status.kind]);
  useEffect(() => {
    if (!showGuide) return;
    const t = window.setTimeout(() => setShowGuide(false), 8000);
    return () => window.clearTimeout(t);
  }, [showGuide]);

  if (!available) return null;

  const deviceId = computeNode?.flops_executor_id ?? null;
  const configured = !!deviceId && deviceId.trim().length > 0;
  const packsDir = computeNode?.packs_dir ?? null;
  const nodeLabel = computeNode?.node_name ?? "this node";
  const target = resolveSourceTarget(pack, packsDir);
  const isOverride = !!pack.source_entry;

  const onClick = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!configured || !target) {
      setShowGuide(true);
      setStatus({
        kind: "err",
        msg: !configured
          ? "flops_executor_id not set"
          : "packs_dir unknown",
      });
      return;
    }
    setShowGuide(false);
    setStatus({ kind: "loading" });
    try {
      const opts = { path: target, deviceId: deviceId as string };
      console.info("[HoloLab] showDocument", opts);
      const r = await flopsShowDocument(opts);
      if (r.success) {
        setStatus({ kind: "ok" });
      } else {
        setStatus({ kind: "err", msg: r.reason ?? "unknown error" });
      }
    } catch (err) {
      setStatus({
        kind: "err",
        msg: (err as Error).message || "call failed",
      });
    }
  };

  const bg = onDark ? "transparent" : "var(--surface)";
  const border = onDark
    ? "1px solid var(--inverse-border)"
    : "1px solid var(--border)";
  const colour =
    status.kind === "ok"
      ? "var(--success)"
      : status.kind === "err"
        ? "var(--error)"
        : onDark
          ? "var(--inverse-muted)"
          : "var(--text-muted)";

  const title = configured
    ? status.kind === "loading"
      ? "opening source in Cocoder…"
      : status.kind === "ok"
        ? "opened in Cocoder"
        : status.kind === "err"
          ? `Jump to source failed: ${status.msg}`
          : `Jump to source · ${target ?? "?"} (device: ${deviceId})`
    : `${nodeLabel} has no Flops executor id — click for details`;

  return (
    <span style={{ position: "relative", display: "inline-block" }}>
      <button
        type="button"
        onClick={onClick}
        disabled={status.kind === "loading"}
        title={title}
        data-hl-open-source=""
        data-hl-configured={configured ? "1" : "0"}
        data-hl-source-kind={isOverride ? "source_entry" : "manifest"}
        style={{
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          width: "var(--control-h-sm)",
          height: "var(--control-h-sm)",
          padding: 0,
          borderRadius: "var(--radius-sm)",
          border,
          background: bg,
          color: colour,
          cursor: status.kind === "loading" ? "wait" : "pointer",
          fontSize: "var(--fs-sm)",
          lineHeight: 1,
          transition:
            "background var(--dur-fast) var(--ease), color var(--dur-fast) var(--ease)",
        }}
      >
        {status.kind === "loading" ? (
          "…"
        ) : status.kind === "ok" ? (
          "✓"
        ) : status.kind === "err" ? (
          "!"
        ) : (
          <CodeGlyph colour={colour} />
        )}
      </button>
      {showGuide && (
        <FlopsExecutorGuide nodeLabel={nodeLabel} variant="header" />
      )}
    </span>
  );
}
