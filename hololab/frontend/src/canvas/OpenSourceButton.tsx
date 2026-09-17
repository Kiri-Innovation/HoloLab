// "Jump to source" — canvas-header code-icon button. Two-tier behaviour:
//
//   Plain click       → the pack's manifest.yaml (``pack.manifest_path``)
//   ⌘/Ctrl + click   → source_entry (if declared) or the manifest's dir
//
// ``manifest_path`` is the resolved absolute path reported by the node —
// no wire-side path construction. Falls back to
// ``{source_dir}/{name}@{version}/manifest.yaml`` when a legacy node
// hasn't sent it yet.
//
// ``source_entry`` (optional manifest field) names the pack's core
// implementation script. Relative paths resolve against the manifest's
// own directory (self-contained + portable); absolute paths are used
// verbatim. When absent the modifier+click opens the manifest's directory
// — useful for user-created packs whose glue scripts sit alongside
// manifest.yaml.
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
   *  clicking shows the guide callout. */
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

/** Join two path pieces with a single ``/``. */
function join(dir: string, rel: string): string {
  return `${dir.replace(/\/+$/, "")}/${rel.replace(/^\/+/, "")}`;
}

/** Normalise a POSIX-style path by collapsing ``.``/``..`` segments.
 *  Used so a manifest with ``source_entry: ../../../foo/bar.py`` resolves
 *  to a clean absolute path the Cobrowser API can find on disk.
 */
function normalisePath(path: string): string {
  const isAbsolute = path.startsWith("/");
  const parts = path.split("/");
  const stack: string[] = [];
  for (const p of parts) {
    if (p === "" || p === ".") continue;
    if (p === "..") {
      if (stack.length > 0 && stack[stack.length - 1] !== "..") {
        stack.pop();
      } else if (!isAbsolute) {
        stack.push("..");
      }
      continue;
    }
    stack.push(p);
  }
  return (isAbsolute ? "/" : "") + stack.join("/");
}

/** Plain-click target: the pack's manifest.yaml.
 *
 *  Prefers the node-reported ``pack.manifest_path`` (authoritative under
 *  the polymorphic pack_dirs contract). Falls back to constructing a path
 *  under ``source_dir`` (legacy repo-layout mode) or the node's primary
 *  ``packsDir``. Exported for test.
 */
export function resolveManifestTarget(
  pack: CatalogPack,
  packsDir: string | null,
): string | null {
  if (pack.manifest_path) return pack.manifest_path;
  const sourceDir = pack.source_dir || packsDir;
  if (!sourceDir) return null;
  return `${sourceDir.replace(/\/+$/, "")}/${pack.name}@${pack.version}/manifest.yaml`;
}

/** ⌘/Ctrl+click target: ``source_entry`` (if declared) or the manifest's dir.
 *
 *  Relative ``source_entry`` paths resolve against the manifest.yaml's own
 *  directory (self-contained + portable across repo reorganisations).
 *  Absolute paths are used verbatim. When absent, opens the manifest's
 *  directory so the operator can see all sibling files at once. Exported
 *  for test.
 */
export function resolveCodeTarget(
  pack: CatalogPack,
  packsDir: string | null,
): string | null {
  const manifestPath = resolveManifestTarget(pack, packsDir);
  if (!manifestPath) return null;
  const manifestDir = manifestPath.replace(/\/[^/]*$/, "") || "/";

  const entry = pack.source_entry;
  if (entry) {
    if (entry.startsWith("/")) return entry;
    return normalisePath(join(manifestDir, entry));
  }
  return manifestDir;
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
