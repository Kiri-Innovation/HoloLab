// Debounced draft autosave.
//
// Watches ``serialisedSnapshot`` (a stringified canonical form of the
// draft — name + graph) and, when it changes, schedules a single
// trailing save 800 ms later. Consecutive edits reset the timer so a
// drag-heavy interaction only produces one save at the end.
//
// Contract with the rest of the app:
//
//   * The saver is a single async function that upserts the draft
//     (POST /api/workflows). This upsert never creates a snapshot —
//     that's what the separate ``/dispatch`` and ``/run`` endpoints
//     are for. See the ``no snapshot side-effect`` invariant checked
//     in the Playwright test.
//   * On success the just-saved serialised form is cached in a ref
//     so a redundant re-render with the same content doesn't re-save.
//   * On failure the status pill flips to ``error``; the pill's
//     onClick handler calls the imperative ``retry`` returned here.
//     Autosave also re-attempts naturally on the next real edit
//     because ``lastSaved`` was NOT updated.
//   * A ``beforeunload`` listener flushes any pending save with
//     ``navigator.sendBeacon`` so a tab close in the debounce window
//     doesn't drop the last edit.
//
// Concurrency: last-write-wins server-side (no version guard). Multi-
// tab edits to the same workflow can silently clobber each other;
// documented in the report shipped with this feature.

import { useCallback, useEffect, useRef, useState } from "react";

export type SaveStatus =
  | "idle" // never dirty / freshly hydrated
  | "unsaved" // dirty; save is scheduled but hasn't fired
  | "saving" // request in flight
  | "saved" // last save succeeded
  | "error" // last save failed; retry on click
  | "offline"; // navigator.onLine is false; will save on ``online``

export interface AutosaveHandle {
  status: SaveStatus;
  // Imperative "flush" — called by the retry pill and by dependency
  // consumers who want a synchronous save (e.g. the Run button
  // pre-flighting before dispatch).
  save: () => Promise<void>;
}

const DEBOUNCE_MS = 800;

export function useDraftAutosave(opts: {
  // Serialised representation of the draft that autosave watches.
  // Empty string means "no meaningful content yet" and skips saving
  // — used to gate the initial mount (before workflow hydration
  // completes) so an empty canvas doesn't fire a save-of-nothing.
  serialisedSnapshot: string;
  // The saver — returns the persisted workflow_id (mints one on
  // first save when the caller didn't have one yet).
  save: () => Promise<{ workflow_id: string }>;
  // For the beforeunload sendBeacon path — the same POST body the
  // in-app saver would use, ready-to-JSON.
  beaconBody: () => { url: string; body: string } | null;
  // If false the hook is dormant (no autosave). Used when a snapshot
  // is being viewed instead of the draft.
  enabled: boolean;
  // Called after a successful save so the caller can, for example,
  // update the URL hash with the minted workflow_id.
  onSaved?: (workflow_id: string) => void;
}): AutosaveHandle {
  const [status, setStatus] = useState<SaveStatus>("idle");
  const lastSavedSerialised = useRef<string>("");
  const inFlight = useRef<Promise<void> | null>(null);
  const savedOnce = useRef<boolean>(false);

  // Public save function — used both by the debounced effect and by
  // the retry button. Serialises against ``inFlight`` so parallel
  // triggers coalesce into one round trip.
  const doSave = useCallback(async () => {
    if (inFlight.current) return inFlight.current;
    const snapshotBeforeSave = opts.serialisedSnapshot;
    setStatus("saving");
    const p = (async () => {
      try {
        const result = await opts.save();
        // Only cache the pre-save snapshot as clean; the state may
        // have advanced during the round trip, in which case the
        // next debounced tick catches those changes.
        lastSavedSerialised.current = snapshotBeforeSave;
        savedOnce.current = true;
        opts.onSaved?.(result.workflow_id);
        // If the state changed while we were saving, the pill flips
        // straight to ``unsaved`` (via the effect below); otherwise
        // it lands on ``saved``.
        setStatus(
          opts.serialisedSnapshot === snapshotBeforeSave ? "saved" : "unsaved",
        );
      } catch (err) {
        console.warn("autosave failed", err);
        setStatus(navigator.onLine === false ? "offline" : "error");
      } finally {
        inFlight.current = null;
      }
    })();
    inFlight.current = p;
    return p;
  }, [opts]);

  // Debounced trigger. Compares the current serialised form to the
  // last-saved one; when they differ, schedule a save. The
  // dependencies are just the primitives the effect actually reads.
  useEffect(() => {
    if (!opts.enabled) return;
    if (opts.serialisedSnapshot === "") return;
    if (opts.serialisedSnapshot === lastSavedSerialised.current) return;
    // First hydration: skip the very first save. Otherwise loading
    // a workflow into the draft would immediately fire a redundant
    // save-of-the-just-loaded-state.
    if (!savedOnce.current && lastSavedSerialised.current === "") {
      lastSavedSerialised.current = opts.serialisedSnapshot;
      return;
    }
    setStatus("unsaved");
    const t = window.setTimeout(() => {
      void doSave();
    }, DEBOUNCE_MS);
    return () => {
      window.clearTimeout(t);
    };
  }, [opts.enabled, opts.serialisedSnapshot, doSave]);

  // ``beforeunload`` flush. sendBeacon is fire-and-forget; the
  // gateway's POST /api/workflows endpoint accepts the same body
  // regardless of whether it was sent via fetch or beacon.
  useEffect(() => {
    if (!opts.enabled) return;
    const handler = () => {
      if (opts.serialisedSnapshot === lastSavedSerialised.current) return;
      const b = opts.beaconBody();
      if (!b) return;
      try {
        const blob = new Blob([b.body], { type: "application/json" });
        navigator.sendBeacon(b.url, blob);
      } catch {
        // The user is closing the tab anyway; nothing we can do.
      }
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [opts.enabled, opts]);

  // Retry on ``online``: if we're in the offline state and the
  // browser reconnects, kick a save.
  useEffect(() => {
    const onOnline = () => {
      if (status === "offline") void doSave();
    };
    window.addEventListener("online", onOnline);
    return () => window.removeEventListener("online", onOnline);
  }, [status, doSave]);

  return { status, save: doSave };
}
