// "Open in Cocoder" — Cobrowser integration entry point on a preview
// drawer / zoom overlay.
//
// The button:
//   * only renders inside Flops Cobrowser (``flopsAvailable()``); a
//     regular Chrome tab never sees it — hiding beats disabled here
//     because the API literally does not exist to describe to the
//     user;
//   * requires the graph node to have a ``flops_executor_id`` cosmetic
//     field set — that's the ``deviceId`` argument the API needs to
//     know which machine's filesystem to target. If unset, the button
//     still renders (so the missing config is visible) but the click
//     inlines a hint instead of firing the API;
//   * translates the API's ``reason`` codes back into short human
//     strings. Never surfaces ``not-found`` / ``outside-roots``
//     details on an unauthorised origin (the spec collapses those to
//     ``declined`` on the host side already, but be defensive).
//
// See docs/cobrowser-integration.md.

import { useEffect, useState } from "react";
import { flopsAvailable, flopsShowDocument } from "../flops";

export interface OpenInCocoderButtonProps {
  /** Producing node's local absolute path (from ``HandleInfo.absolute_path``). */
  path: string;
  /**
   * The graph node's cosmetic ``flops_executor_id``. Empty / null means
   * "user hasn't configured which device this artifact lives on";
   * we still render the button but a click shows a hint.
   */
  deviceId: string | null;
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
  deviceId,
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

  // Reset the flash back to idle after a few seconds so a stale
  // success / error indicator doesn't linger.
  useEffect(() => {
    if (status.kind === "ok" || status.kind === "err") {
      const t = window.setTimeout(() => setStatus({ kind: "idle" }), 3500);
      return () => window.clearTimeout(t);
    }
  }, [status.kind]);

  if (!available) return null;

  const configured = !!deviceId && deviceId.trim().length > 0;

  const onClick = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!configured) {
      setStatus({
        kind: "err",
        msg: "set flops_executor_id in node settings",
      });
      return;
    }
    setStatus({ kind: "loading" });
    try {
      const r = await flopsShowDocument({
        path,
        deviceId: deviceId as string,
      });
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
    : "open in Cocoder — set flops_executor_id in node settings first";

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={status.kind === "loading"}
      title={title}
      data-hl-open-cocoder=""
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
  );
}
