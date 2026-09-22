// Debounced draft autosave.
//
// Watches ``serialisedSnapshot`` (a stringified canonical form of the
// draft — name + graph) and, when it differs from the last-known-clean
// snapshot, schedules a single trailing save 800 ms later. Consecutive
// edits reset the timer so a drag-heavy interaction only produces one
// save at the end.
//
// Layers of the "no stale-tab clobber" defence this hook participates in:
//
//   1. Dirty diff — the hook compares the current serialised form against
//      the last snapshot the caller marked clean (via ``markClean`` on
//      hydration or on WS-driven remote-update sync). Autosave only
//      fires when the strings actually differ, so hydration / catalog-
//      churn / WS resync no longer misfires a save-of-nothing that
//      could POST an in-memory graph on top of fresher truth.
//   2. Optimistic-lock CAS — every save carries the last-observed
//      ``base_updated_ts``. Server 409 → hook parks in ``conflict``.
//   3. Precondition Required — a save on an existing row without a
//      base_ts (stale bundle) returns 428; the hook also parks in
//      ``conflict`` and fires ``onConflict`` with the current row so
//      the caller can prompt reload.
//   4. Remote-update push — the caller subscribes to
//      ``workflow_updated`` WS frames and calls ``resync`` (silent
//      sync when the tab was clean) or ``markConflict`` (dirty tab
//      loses its autosave gate until the user resolves).
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

import { useCallback, useEffect, useRef, useState } from "react";

import {
  isWorkflowConflict,
  isWorkflowPreconditionRequired,
  type WorkflowConflictDetail,
  type WorkflowPreconditionRequiredDetail,
} from "../api";

export type SaveStatus =
  | "idle" // never dirty / freshly hydrated
  | "unsaved" // dirty; save is scheduled but hasn't fired
  | "saving" // request in flight
  | "saved" // last save succeeded
  | "error" // last save failed; retry on click
  | "offline" // navigator.onLine is false; will save on ``online``
  | "conflict"; // server rejected save (409 or 428); autosave is parked

// Public payload the caller uses when a remote WS push tells us the row
// moved on someone else's account. Same shape as the 409 detail so the
// caller can hand it straight to the same reload UX.
export interface RemoteUpdateInfo {
  workflow_id: string;
  updated_ts: number;
  name: string;
}

export interface AutosaveHandle {
  status: SaveStatus;
  // Imperative "flush" — called by the retry pill and by dependency
  // consumers who want a synchronous save (e.g. the Run button
  // pre-flighting before dispatch).
  save: () => Promise<void>;
  // Imperative "the loaded state is now clean" — called after a
  // hydration where the graph in memory matches server truth (initial
  // load, workflow switch, WS-driven silent sync). Resets the hook to
  // a fresh-mount state pinned at ``updatedTs``; the next serialised
  // form the effect sees is stamped as the new baseline, so a user
  // edit is the first thing that fires a save.
  resync: (updatedTs: number | undefined) => void;
  // Imperative "server has diverged and we can't merge" — called when
  // the caller detects (via WS or otherwise) that the row moved on
  // while this tab has unsaved edits. Parks the hook in ``conflict``
  // and fires ``onConflict`` with the detail so the app can prompt
  // reload / overwrite.
  markConflict: (detail: WorkflowConflictDetail) => void;
}

const DEBOUNCE_MS = 800;

export function useDraftAutosave(opts: {
  // Serialised representation of the draft that autosave watches.
  // Empty string means "no meaningful content yet" and skips saving
  // — used to gate the initial mount (before workflow hydration
  // completes) so an empty canvas doesn't fire a save-of-nothing.
  serialisedSnapshot: string;
  // The saver — receives the ``base_updated_ts`` the hook last saw
  // (undefined for a brand-new draft). Returns the persisted
  // workflow_id and the fresh ``updated_ts`` for the next save.
  save: (baseUpdatedTs: number | undefined) => Promise<{
    workflow_id: string;
    updated_ts: number;
  }>;
  // For the beforeunload sendBeacon path — the same POST body the
  // in-app saver would use, ready-to-JSON. Receives the current
  // ``base_updated_ts`` so the beacon carries the same guard.
  beaconBody: (baseUpdatedTs: number | undefined) =>
    | { url: string; body: string }
    | null;
  // If false the hook is dormant (no autosave). Used when a snapshot
  // is being viewed instead of the draft.
  enabled: boolean;
  // Called after a successful save so the caller can, for example,
  // update the URL hash with the minted workflow_id.
  onSaved?: (workflow_id: string, updated_ts: number) => void;
  // Called when the server returns 409 or 428, OR when the caller
  // pushes a ``markConflict`` in response to a WS ``workflow_updated``
  // arriving on a dirty tab. Until the caller resolves it (typically
  // by reloading the fresh draft), autosave stays parked in
  // ``conflict``.
  onConflict?: (
    detail: WorkflowConflictDetail | WorkflowPreconditionRequiredDetail,
  ) => void;
  // Initial ``updated_ts`` for the loaded draft, so the FIRST save
  // includes it. ``undefined`` means "this is a brand-new draft, no
  // guard yet" (first save will mint the row + a ts).
  initialBaseUpdatedTs?: number;
}): AutosaveHandle {
  const [status, setStatus] = useState<SaveStatus>("idle");
  const lastSavedSerialised = useRef<string>("");
  const inFlight = useRef<Promise<void> | null>(null);
  const savedOnce = useRef<boolean>(false);
  // Tracks the ``updated_ts`` for the row we currently know about —
  // seeded from the load and bumped on each successful save. The next
  // save sends this as ``base_updated_ts`` so the server can catch
  // a concurrent writer.
  const baseUpdatedTs = useRef<number | undefined>(opts.initialBaseUpdatedTs);

  // Public save function — used both by the debounced effect and by
  // the retry button. Serialises against ``inFlight`` so parallel
  // triggers coalesce into one round trip.
  const doSave = useCallback(async () => {
    if (inFlight.current) return inFlight.current;
    const snapshotBeforeSave = opts.serialisedSnapshot;
    setStatus("saving");
    const p = (async () => {
      try {
        const result = await opts.save(baseUpdatedTs.current);
        // Only cache the pre-save snapshot as clean; the state may
        // have advanced during the round trip, in which case the
        // next debounced tick catches those changes.
        lastSavedSerialised.current = snapshotBeforeSave;
        savedOnce.current = true;
        baseUpdatedTs.current = result.updated_ts;
        opts.onSaved?.(result.workflow_id, result.updated_ts);
        // If the state changed while we were saving, the pill flips
        // straight to ``unsaved`` (via the effect below); otherwise
        // it lands on ``saved``.
        setStatus(
          opts.serialisedSnapshot === snapshotBeforeSave ? "saved" : "unsaved",
        );
      } catch (err) {
        if (isWorkflowConflict(err)) {
          console.warn("autosave conflict — server row has moved past ours", err);
          setStatus("conflict");
          opts.onConflict?.(err.detail);
        } else if (isWorkflowPreconditionRequired(err)) {
          // Legacy tab / stale bundle: we sent no base_updated_ts and
          // the row exists. The server refused rather than clobber. Park
          // in conflict so the pill goes red — the recovery path is a
          // reload that re-hydrates the fresh draft and mints a base_ts.
          console.warn(
            "autosave rejected — save missed base_updated_ts on an existing row",
            err,
          );
          setStatus("conflict");
          opts.onConflict?.(err.detail);
        } else {
          console.warn("autosave failed", err);
          setStatus(navigator.onLine === false ? "offline" : "error");
        }
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
    // In ``conflict`` we've been told our base is stale — retrying
    // just re-fires the same 409/428. Park until the caller resolves it
    // (usually by hydrating the fresh draft and remounting / markClean).
    if (status === "conflict") return;
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
  }, [opts.enabled, opts.serialisedSnapshot, doSave, status]);

  // ``beforeunload`` flush. sendBeacon is fire-and-forget; the
  // gateway's POST /api/workflows endpoint accepts the same body
  // regardless of whether it was sent via fetch or beacon.
  useEffect(() => {
    if (!opts.enabled) return;
    const handler = () => {
      // Don't fire the beacon in ``conflict`` — the row has moved on,
      // sending our stale base_updated_ts just gets rejected again.
      if (status === "conflict") return;
      if (opts.serialisedSnapshot === lastSavedSerialised.current) return;
      const b = opts.beaconBody(baseUpdatedTs.current);
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
  }, [opts.enabled, opts, status]);

  // Retry on ``online``: if we're in the offline state and the
  // browser reconnects, kick a save.
  useEffect(() => {
    const onOnline = () => {
      if (status === "offline") void doSave();
    };
    window.addEventListener("online", onOnline);
    return () => window.removeEventListener("online", onOnline);
  }, [status, doSave]);

  const resync = useCallback((updatedTs: number | undefined) => {
    // Reset to fresh-mount state. Reusing the first-hydration guard in
    // the effect above: the next non-empty serialised form the effect
    // observes will be stamped as the new ``lastSavedSerialised`` and
    // no save fires. Callers should call this BEFORE they mutate the
    // draft state (so the effect's next tick reads the loaded state,
    // not the previous workflow's).
    lastSavedSerialised.current = "";
    savedOnce.current = false;
    baseUpdatedTs.current = updatedTs;
    setStatus("idle");
  }, []);

  const markConflict = useCallback(
    (detail: WorkflowConflictDetail) => {
      setStatus("conflict");
      opts.onConflict?.(detail);
    },
    [opts],
  );

  return { status, save: doSave, resync, markConflict };
}
