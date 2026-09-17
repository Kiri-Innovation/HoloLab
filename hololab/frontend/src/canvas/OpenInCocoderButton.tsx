// "Open in Cocoder" — Cobrowser integration entry point on a preview
// drawer / zoom overlay.
//
// The button:
//   * only renders inside Flops Cobrowser (``flopsAvailable()``); a
//     regular Chrome tab never sees it — hiding beats disabled here
//     because the API literally does not exist to describe to the
//     user;
//   * targets the **producing compute node** rather than the graph
//     node — the ``deviceId`` for ``window.flops.showDocument`` comes
//     from that compute node's ``flops_executor_id`` (set in the
//     right-side COMPUTE NODES panel → gear → Node settings). This is
//     a live lookup: an edit in Node settings is reflected on the next
//     ``GET /api/nodes`` poll without needing to re-run the job;
//   * when the producing node has no ``flops_executor_id``, the click
//     does NOT hit the API. Instead the button expands a *visible*
//     guide message beneath itself pointing at the panel + the exact
//     field to fill — a hover tooltip alone (previous behaviour) is
//     easy to miss;
//   * on success / error paths, humanises the ``reason`` code from
//     the API and flashes a status.
//
// See docs/cobrowser-integration.md.

import { useEffect, useState } from "react";
import type { ComputeNode } from "../wire";
import { flopsAvailable, flopsShowDocument } from "../flops";
import { FlopsExecutorGuide } from "./FlopsExecutorGuide";

export interface OpenInCocoderButtonProps {
  /** Producing node's local absolute path (from ``HandleInfo.absolute_path``). */
  path: string;
  /**
   * The compute node that produced this handle. The button reads
   * ``flops_executor_id`` off it at render time so a NodeSettingsDrawer
   * edit is visible immediately (no autosave round-trip needed). Null
   * = producing node not connected right now → button still renders
   * so the user can find the missing config, but shows an explanatory
   * message instead of firing the API.
   */
  computeNode: ComputeNode | null;
  /** Match ``CopyRefButton``'s onDark flag — this button lives inside
   *  the dark preview drawer + zoom overlay. */
  onDark?: boolean;
}

type Status =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ok" }
  | { kind: "err"; msg: string };

/** Best-effort humanisation of the API's ``reason`` codes. See
 *  ``cobrowser-web-api.md`` §4.2 for the source of truth.
 */
function reasonToMessage(reason: string | undefined): string {
  switch (reason) {
    case "declined":
      return "cancelled";
    case "busy":
      return "another request is waiting for confirmation";
    case "timeout":
      return "confirmation card timed out";
    case "invalid-path":
      return "invalid path (bug)";
    case "forbidden":
      return "page origin cannot use this API";
    case "not-found":
      return "path not found on device";
    case "outside-roots":
      return "path is not inside a Cocoder workspace root";
    case "unsupported-kind":
      return "path is neither file nor directory";
    case "unknown-window":
      return "tab was closed";
    case "unavailable":
    case "bad-response":
    case "error":
      return "internal error";
    default:
      return reason ?? "unknown error";
  }
}

export function OpenInCocoderButton({
  path,
  computeNode,
  onDark = false,
}: OpenInCocoderButtonProps) {
  // Cheap availability re-check on mount — Cobrowser injects
  // ``window.flops`` before any script runs, so it's usually resolved
  // by first render, but a defensive check keeps the "regular
  // browser" path from ever exposing a broken button.
  const [available, setAvailable] = useState<boolean>(() => flopsAvailable());
  useEffect(() => {
    if (!available && flopsAvailable()) setAvailable(true);
  }, [available]);

  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const [showGuide, setShowGuide] = useState<boolean>(false);

  // Reset the flash back to idle after a few seconds so a stale
  // success / error indicator doesn't linger. The guide callout has
  // its own timeout (a bit longer — the user needs to read it).
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
  const nodeLabel = computeNode?.node_name ?? "this node";

  const onClick = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!configured) {
      setShowGuide(true);
      setStatus({ kind: "err", msg: "flops_executor_id not set" });
      return;
    }
    setShowGuide(false);
    setStatus({ kind: "loading" });
    try {
      const opts = { path, deviceId: deviceId as string };
      console.info("[HoloLab] showDocument", opts);
      const r = await flopsShowDocument(opts);
      if (r.success) {
        setStatus({ kind: "ok" });
      } else {
        setStatus({ kind: "err", msg: reasonToMessage(r.reason) });
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
  const color =
    status.kind === "ok"
      ? "var(--success)"
      : status.kind === "err"
        ? "var(--error)"
        : onDark
          ? "var(--inverse-muted)"
          : "var(--text-muted)";

  const icon =
    status.kind === "loading"
      ? "…"
      : status.kind === "ok"
        ? "✓"
        : status.kind === "err"
          ? "!"
          : "↗";

  const title = configured
    ? status.kind === "loading"
      ? "opening in Cocoder…"
      : status.kind === "ok"
        ? "opened in Cocoder"
        : status.kind === "err"
          ? `open in Cocoder failed: ${status.msg}`
          : `open in Cocoder · ${path} (device: ${deviceId})`
    : `${nodeLabel} has no Flops executor id — click for details`;

  return (
    // Position: relative so the guide callout can anchor itself to
    // this button. Inline-block so it doesn't disturb the flexbox
    // layout inside the drawer header.
    <span style={{ position: "relative", display: "inline-block" }}>
      <button
        type="button"
        onClick={onClick}
        disabled={status.kind === "loading"}
        title={title}
        data-hl-open-cocoder=""
        data-hl-configured={configured ? "1" : "0"}
        style={{
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          gap: 4,
          width: "var(--control-h-sm)",
          height: "var(--control-h-sm)",
          padding: 0,
          borderRadius: "var(--radius-sm)",
          border,
          background: bg,
          color,
          cursor: status.kind === "loading" ? "wait" : "pointer",
          fontSize: "var(--fs-sm)",
          lineHeight: 1,
          transition:
            "background var(--dur-fast) var(--ease), color var(--dur-fast) var(--ease)",
        }}
      >
        {icon}
      </button>
      {showGuide && <FlopsExecutorGuide nodeLabel={nodeLabel} />}
    </span>
  );
}
