// Flops Cobrowser integration — types + env detection.
//
// The Flops built-in browser injects a frozen ``window.flops`` object
// on top-frame http(s) pages. When present, we can ask the host to
// open a local file/folder in Cocoder via ``showDocument``. The full
// spec lives at ``temp/cobrowser-web-api.md`` (mirrored in a future
// docs/cobrowser-integration.md) — this module wraps the two things
// the HoloLab frontend needs:
//
//   1. A cheap availability check so we can hide the "Open in
//      Cocoder" button + the ``flops_executor_id`` settings field in
//      any non-Cobrowser environment (regular Chrome / Safari).
//   2. A typed ``showDocument`` call so callers don't have to reach
//      into a loose ``any`` on ``window``.

export interface FlopsShowDocumentParams {
  /** Absolute path on the target device's filesystem. */
  path: string;
  /**
   * Flops device id the path belongs to. Omit only when the browser
   * tab and the target file live on the same machine (Cobrowser
   * defaults to "this tab's device" for an absent id) — which for
   * HoloLab is almost never the case, since the Cobrowser tab runs
   * on the user's Mac and produced handles live on the compute node.
   * Callers should read this from the graph node's cosmetic
   * ``flops_executor_id`` field.
   */
  deviceId?: string;
  /** LSP-style zero-based line range; ignored for directories. */
  selection?: {
    start: { line: number; character: number };
    end: { line: number; character: number };
  };
}

export interface FlopsShowDocumentResult {
  success: boolean;
  reason?: string;
  kind?: "file" | "dir";
}

export interface FlopsInsertReferenceParams {
  schemaVersion: 1;
  provider: string;
  type: string;
  requestId?: string;
  resource: { uri: string; revision?: string };
  display?: {
    title?: string;
    subtitle?: string;
    icon?: "external" | "document" | "issue" | "workflow" | "dataset" | "link";
  };
  access?:
    | { mode: "link_only" }
    | { mode: "public_https"; url?: string; mediaType?: string };
  documentation?: { url: string };
  snapshot?: { text: string; mediaType: "text/plain" | "text/markdown" };
}

export interface FlopsInsertReferenceResult {
  success: boolean;
  requestId?: string;
  kind?: string;
  outcome?: "inserted" | "already_present";
  reason?: string;
}

interface FlopsWindow {
  version: number;
  showDocument: (
    params: FlopsShowDocumentParams,
  ) => Promise<FlopsShowDocumentResult>;
  insertReference: (
    params: FlopsInsertReferenceParams,
  ) => Promise<FlopsInsertReferenceResult>;
}

/** True only when the CoBrowser supports insert-reference v1. */
export function flopsInsertReferenceAvailable(): boolean {
  const f = window.flops;
  return !!f && f.version === 1 && typeof f.insertReference === "function";
}

/** Typed insert-reference wrapper. Callers should gate UI with
 * ``flopsInsertReferenceAvailable`` so regular browsers never expose a
 * non-functional action. */
export async function flopsInsertReference(
  params: FlopsInsertReferenceParams,
): Promise<FlopsInsertReferenceResult> {
  const f = window.flops;
  if (!f || typeof f.insertReference !== "function") {
    throw new Error("window.flops.insertReference is not available");
  }
  return f.insertReference(params);
}

declare global {
  interface Window {
    flops?: FlopsWindow;
  }
}

/** True when the current page has a usable ``window.flops.showDocument``.
 *
 *  The spec guarantees the object is either absent or frozen with
 *  ``version === 1`` today; we match on both so a future major-version
 *  bump doesn't silently fire the current code path against an
 *  incompatible shape.
 */
export function flopsAvailable(): boolean {
  const f = window.flops;
  return (
    !!f &&
    f.version === 1 &&
    typeof f.showDocument === "function"
  );
}

/** Convenience wrapper — same signature as ``window.flops.showDocument``
 *  but callable without a null check. Throws when Flops isn't available
 *  so callers can gate on ``flopsAvailable()`` first; UI already hides
 *  the trigger element in that case, so a throw here just surfaces a
 *  programmer error.
 */
export async function flopsShowDocument(
  params: FlopsShowDocumentParams,
): Promise<FlopsShowDocumentResult> {
  const f = window.flops;
  if (!f || typeof f.showDocument !== "function") {
    throw new Error("window.flops.showDocument is not available");
  }
  return f.showDocument(params);
}
