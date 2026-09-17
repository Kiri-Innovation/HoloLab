// "Jump to source" — canvas-header code-icon button. Two-tier behaviour:
//
//   Plain click       → {source_dir}/{name}@{version}/manifest.yaml
//   ⌘/Ctrl + click   → source_entry (if declared) or the pack directory
//
// ``source_dir`` comes from the pack catalog (the exact pack_dirs entry
// this pack was loaded from). Falls back to the node's primary ``packs_dir``
// for nodes that predate multi-pack-source support.
//
// ``source_entry`` is an optional manifest field naming the pack's core
// implementation script. Relative paths resolve against the Kiri4DGS repo
// root (``source_dir.parent.parent`` in the standard layout); absolute
// paths are used verbatim. When absent the modifier+click falls back to
// opening the pack directory itself — useful for user-created packs whose
// glue scripts sit alongside manifest.yaml.
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

/** Plain-click target: always manifest.yaml.
 *
 *  ``pack.source_dir`` is preferred (the exact pack_dirs entry this pack
 *  was loaded from); ``packsDir`` (the node's primary packs dir) is the
 *  fallback for nodes that predate multi-pack-source support. Exported for test.
 */
export function resolveManifestTarget(
  pack: CatalogPack,
  packsDir: string | null,
): string | null {
  const sourceDir = pack.source_dir || packsDir;
  if (!sourceDir) return null;
  return `${sourceDir.replace(/\/+$/, "")}/${pack.name}@${pack.version}/manifest.yaml`;
}

/** ⌘/Ctrl+click target: ``source_entry`` (if declared) or the pack directory.
 *
 *  Relative ``source_entry`` paths resolve against the Kiri4DGS repo root,
 *  derived as ``sourceDir.parent.parent`` (packs live two levels deep under
 *  the repo, e.g. ``<repo>/hololab/packs/``). Exported for test.
 */
export function resolveCodeTarget(
  pack: CatalogPack,
  packsDir: string | null,
): string | null {
  const sourceDir = pack.source_dir || packsDir;
  if (!sourceDir) return null;
  const trimmed = sourceDir.replace(/\/+$/, "");

  const entry = pack.source_entry;
  if (entry) {
    if (entry.startsWith("/")) return entry;
    // Relative → repo root = strip last 2 path components from sourceDir.
    const parts = trimmed.split("/");
    if (parts.length < 3) return null;
    const repoRoot = parts.slice(0, -2).join("/");
    return `${repoRoot}/${entry}`;
  }

  // No source_entry → open the pack directory so the operator can see
  // all the files (glue scripts, templates, etc.) alongside manifest.yaml.
  return `${trimmed}/${pack.name}@${pack.version}`;
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
  const manifestTarget = resolveManifestTarget(pack, packsDir);
  const codeTarget = resolveCodeTarget(pack, packsDir);
  const hasSourceEntry = !!pack.source_entry;

  const onClick = async (e: React.MouseEvent) => {
    e.stopPropagation();
    const isModified = e.metaKey || e.ctrlKey;
    const target = isModified ? codeTarget : manifestTarget;
    if (!configured || !target) {
      setShowGuide(true);
      setStatus({
        kind: "err",
        msg: !configured
          ? "flops_executor_id not set"
          : "source directory unknown",
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

  const codeHint = hasSourceEntry
    ? "⌘/Ctrl+click: code file"
    : "⌘/Ctrl+click: pack dir";
  const title = configured
    ? status.kind === "loading"
      ? "opening in Cocoder…"
      : status.kind === "ok"
        ? "opened in Cocoder"
        : status.kind === "err"
          ? `Jump to source failed: ${status.msg}`
          : `Jump to source · click: manifest.yaml · ${codeHint} (device: ${deviceId})`
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
        data-hl-source-has-entry={hasSourceEntry ? "1" : "0"}
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
