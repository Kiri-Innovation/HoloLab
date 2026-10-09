// "Jump to artifact location" — footer-scale sibling of
// OpenInCocoderButton. Uses the same compact 24 px control as the
// preview toggle and node header actions. Opens the producing node's on-disk artifact
// path/directory in Cocoder via ``window.flops.showDocument``.
//
// The button:
//   * only renders inside Flops Cobrowser (``flopsAvailable()``) — same
//     rule as the drawer's ↗ button;
//   * targets the ``PreviewTarget.absolute_path`` of the FIRST output
//     port that has resolved artifacts (i.e. ``previews[port]``). Multi-
//     output cases are handled inside the drawer's per-port headers
//     where each port has its own Open-in-Cocoder;
//   * reads ``deviceId`` off the producing compute node
//     (``PreviewTarget.node_id``) — the same lookup pattern the drawer
//     uses. Falls back to the currently-assigned node when the producing
//     node isn't online (rare, but keeps the button clickable on stale
//     handles);
//   * shows a flat error state (red icon + title) rather than a floating
//     callout to keep feedback within the compact footer. The full Cocoder flow (with guide
//     + callout) is still available in the drawer.
//
// See docs/cobrowser-integration.md.

import { useEffect, useState } from "react";
import { ArtifactJumpGlyph } from "./ArtifactJumpGlyph";
import type { ComputeNode } from "../wire";
import { flopsAvailable, flopsShowDocument } from "../flops";

export interface OpenArtifactLocationButtonProps {
  /** Producing node's local absolute path — passed straight into
   *  ``window.flops.showDocument``. */
  path: string;
  /** The compute node that produced this handle. Its
   *  ``flops_executor_id`` is the ``deviceId`` for the ``showDocument``
   *  call. Null when the producing node is not online right now — the
   *  button still renders (so the operator sees the entry point) but
   *  the click surfaces a config-missing error via the title. */
  computeNode: ComputeNode | null;
}

type Status =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ok" }
  | { kind: "err"; msg: string };

export function OpenArtifactLocationButton({
  path,
  computeNode,
}: OpenArtifactLocationButtonProps) {
  const [available, setAvailable] = useState<boolean>(() => flopsAvailable());
  useEffect(() => {
    if (!available && flopsAvailable()) setAvailable(true);
  }, [available]);

  const [status, setStatus] = useState<Status>({ kind: "idle" });

  useEffect(() => {
    if (status.kind === "ok" || status.kind === "err") {
      const t = window.setTimeout(() => setStatus({ kind: "idle" }), 2500);
      return () => window.clearTimeout(t);
    }
  }, [status.kind]);

  if (!available) return null;

  const deviceId = computeNode?.flops_executor_id ?? null;
  const configured = !!deviceId && deviceId.trim().length > 0;
  const nodeLabel = computeNode?.node_name ?? "producing node";

  const onClick = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!configured) {
      setStatus({
        kind: "err",
        msg: `${nodeLabel} has no flops_executor_id — see Compute nodes → ⚙`,
      });
      return;
    }
    setStatus({ kind: "loading" });
    try {
      const opts = { path, deviceId: deviceId as string };
      console.info("[HoloLab] showDocument (artifact)", opts);
      const r = await flopsShowDocument(opts);
      if (r.success) {
        setStatus({ kind: "ok" });
      } else {
        setStatus({ kind: "err", msg: r.reason ?? "open failed" });
      }
    } catch (err) {
      setStatus({ kind: "err", msg: (err as Error).message || "call failed" });
    }
  };

  const colour =
    status.kind === "ok"
      ? "var(--success)"
      : status.kind === "err"
        ? "var(--error)"
        : "var(--text-muted)";

  const title =
    status.kind === "loading"
      ? "opening in Cocoder…"
      : status.kind === "ok"
        ? `opened in Cocoder · ${path}`
        : status.kind === "err"
          ? `open failed: ${status.msg}`
          : configured
            ? `跳转产物位置 · ${path} (device: ${deviceId})`
            : `跳转产物位置 · ${path}\n${nodeLabel} has no flops_executor_id`;

  return (
    <button
      type="button"
      className="hl-icon-button"
      onClick={onClick}
      disabled={status.kind === "loading"}
      data-tooltip={status.kind === "err" ? status.msg : "跳转产物"}
      aria-label="跳转产物"
      aria-description={title}
      data-hl-open-artifact=""
      data-hl-configured={configured ? "1" : "0"}
      style={{
        border: "1px solid var(--border-strong)",
        background: "transparent",
        color: colour,
        borderRadius: "var(--radius-sm)",
        cursor: status.kind === "loading" ? "wait" : "pointer",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 0,
        flexShrink: 0,
        lineHeight: 1,
        transition:
          "color var(--dur-fast) var(--ease), border-color var(--dur-fast) var(--ease)",
      }}
    >
      {status.kind === "loading" ? (
        <span style={{ fontSize: 9 }}>…</span>
      ) : status.kind === "ok" ? (
        <span style={{ fontSize: 9 }}>✓</span>
      ) : status.kind === "err" ? (
        <span style={{ fontSize: 9 }}>!</span>
      ) : (
        <ArtifactJumpGlyph />
      )}
    </button>
  );
}
