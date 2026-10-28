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
const SAVE_TIMEOUT_MS = 30_000;

export function useDraftAutosave(opts: {
  manual?: boolean;
  // Serialised representation of the draft that autosave watches.
  // Empty string means "no meaningful content yet" and skips saving
  // — used to gate the initial mount (before workflow hydration
  // completes) so an empty canvas doesn't fire a save-of-nothing.
  serialisedSnapshot: string;
  // The saver — receives the ``base_updated_ts`` the hook last saw
  // (undefined for a brand-new draft). Returns the persisted
  // workflow_id and the fresh ``updated_ts`` for the next save.
  save: (baseUpdatedTs: number | undefined, signal: AbortSignal) => Promise<{
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
  // App recreates its callback bundle on unrelated live-state renders
  // (metrics, job frames, preview hydration). Keep the debounce stable and
  // read the newest callbacks through a ref, otherwise those renders can
  // continually cancel a pending drag-position save.
  const optsRef = useRef(opts);
  optsRef.current = opts;
  const [status, setStatus] = useState<SaveStatus>("idle");
  const statusRef = useRef<SaveStatus>("idle");
  const updateStatus = useCallback((next: SaveStatus) => {
    statusRef.current = next;
    setStatus(next);
  }, []);
  const [syncRevision, setSyncRevision] = useState(0);
  const lastSavedSerialised = useRef<string>("");
  const inFlight = useRef<Promise<void> | null>(null);
  const debounceTimer = useRef<number | undefined>(undefined);
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
    window.clearTimeout(debounceTimer.current);
    debounceTimer.current = undefined;
    // A Run/retry/visibility flush must include edits made during the previous
    // request, using its returned CAS version rather than racing it.
    while (inFlight.current) {
      await inFlight.current;
      if (optsRef.current.serialisedSnapshot === lastSavedSerialised.current) return;
    }
    if (!optsRef.current.enabled) return;
    if (statusRef.current === "conflict") throw new Error("Workflow conflict: reload before saving or running");
    const current = optsRef.current;
    const snapshotBeforeSave = current.serialisedSnapshot;
    updateStatus("saving");
    const controller = new AbortController();
    let timeout: number | undefined;
    const p = (async () => {
      try {
        // Bound the entire operation, including reading the response body.
        // Racing also releases the UI if a saver ignores the abort signal.
        const result = await Promise.race([
          current.save(baseUpdatedTs.current, controller.signal),
          new Promise<never>((_, reject) => {
            timeout = window.setTimeout(() => {
              reject(new Error("Saving timed out after 30 seconds. Retry to check the current version."));
              controller.abort();
            }, SAVE_TIMEOUT_MS);
          }),
        ]);
        // Only cache the pre-save snapshot as clean; the state may
        // have advanced during the round trip, in which case the
        // next debounced tick catches those changes.
        lastSavedSerialised.current = snapshotBeforeSave;
        savedOnce.current = true;
        baseUpdatedTs.current = result.updated_ts;
        current.onSaved?.(result.workflow_id, result.updated_ts);
        // If the state changed while we were saving, the pill flips
        // straight to ``unsaved`` (via the effect below); otherwise
        // it lands on ``saved``.
        if (statusRef.current !== "conflict") {
          updateStatus(optsRef.current.serialisedSnapshot === snapshotBeforeSave ? "saved" : "unsaved");
          // Schedule a trailing save if edits arrived while this request ran.
          if (optsRef.current.serialisedSnapshot !== snapshotBeforeSave) setSyncRevision(n => n + 1);
        }
      } catch (err) {
        if (isWorkflowConflict(err)) {
          console.warn("autosave conflict — server row has moved past ours", err);
          updateStatus("conflict");
          optsRef.current.onConflict?.(err.detail);
        } else if (isWorkflowPreconditionRequired(err)) {
          // Legacy tab / stale bundle: we sent no base_updated_ts and
          // the row exists. The server refused rather than clobber. Park
          // in conflict so the pill goes red — the recovery path is a
          // reload that re-hydrates the fresh draft and mints a base_ts.
          console.warn(
            "autosave rejected — save missed base_updated_ts on an existing row",
            err,
          );
          updateStatus("conflict");
          optsRef.current.onConflict?.(err.detail);
        } else {
          console.warn("autosave failed", err);
          updateStatus(navigator.onLine === false ? "offline" : "error");
        }
        // Imperative callers (notably Run) must not proceed with an old draft.
        throw err;
      } finally {
        window.clearTimeout(timeout);
        inFlight.current = null;
      }
    })();
    inFlight.current = p;
    return p;
  }, []);

  // Debounced trigger. Compares the current serialised form to the
  // last-saved one; when they differ, schedule a save. The
  // dependencies are just the primitives the effect actually reads.
  useEffect(() => {
    if (!opts.enabled) return;
    // In ``conflict`` we've been told our base is stale — retrying
    // just re-fires the same 409/428. Park until the caller resolves it
    // (usually by hydrating the fresh draft and remounting / markClean).
    if (statusRef.current === "conflict") return;
    if (opts.serialisedSnapshot === "") return;
    if (opts.serialisedSnapshot === lastSavedSerialised.current) return;
    // First hydration: skip the very first save. Otherwise loading
    // a workflow into the draft would immediately fire a redundant
    // save-of-the-just-loaded-state.
    if (!savedOnce.current && lastSavedSerialised.current === "") {
      lastSavedSerialised.current = opts.serialisedSnapshot;
      return;
    }
    updateStatus("unsaved");
    if (opts.manual) return;
    const t = window.setTimeout(() => {
      void doSave().catch(() => { /* status + conflict dialog report the failure */ });
    }, DEBOUNCE_MS);
    debounceTimer.current = t;
    return () => {
      window.clearTimeout(t);
    };
  }, [opts.enabled, opts.manual, opts.serialisedSnapshot, doSave, syncRevision]);

  // Flush on backgrounding while responses can still be observed. On unload,
  // beacon is best-effort and carries the same CAS precondition; never race an
  // in-flight save with a second request using its old version.
  useEffect(() => {
    const dirty = () => optsRef.current.enabled && !optsRef.current.manual
      && optsRef.current.serialisedSnapshot !== ""
      && optsRef.current.serialisedSnapshot !== lastSavedSerialised.current;
    const onHidden = () => {
      if (document.visibilityState === "hidden" && dirty() && statusRef.current !== "conflict") {
        void doSave().catch(() => { /* keep failure visible when returning */ });
      }
    };
    const onUnload = (event: BeforeUnloadEvent) => {
      // The existing conflict dialog explicitly warns about discarding edits.
      // Do not block its Reload action with a second native confirmation.
      if (!dirty() || statusRef.current === "conflict") return;
      let queued = false;
      if (!inFlight.current) {
        const b = optsRef.current.beaconBody(baseUpdatedTs.current);
        if (b) {
          try {
            queued = navigator.sendBeacon(b.url, new Blob([b.body], {type: "application/json"}));
          } catch { /* blocked / over quota: warn instead of claiming success */ }
        }
      }
      if (!queued) {
        // Includes in-flight requests and the browser's beacon quota.
        event.preventDefault();
        event.returnValue = "";
      }
    };
    document.addEventListener("visibilitychange", onHidden);
    window.addEventListener("beforeunload", onUnload);
    return () => {
      document.removeEventListener("visibilitychange", onHidden);
      window.removeEventListener("beforeunload", onUnload);
    };
  }, [doSave]);

  // Retry on ``online``: if we're in the offline state and the
  // browser reconnects, kick a save.
  useEffect(() => {
    const onOnline = () => {
      if (statusRef.current === "offline" && !optsRef.current.manual) void doSave().catch(() => {});
    };
    window.addEventListener("online", onOnline);
    return () => window.removeEventListener("online", onOnline);
  }, [doSave]);

  const resync = useCallback((updatedTs: number | undefined) => {
    // Reset to fresh-mount state. Reusing the first-hydration guard in
    // the effect above: the next non-empty serialised form the effect
    // observes will be stamped as the new ``lastSavedSerialised`` and
    // no save fires. Callers should call this BEFORE they mutate the
    // draft state (so the effect's next tick reads the loaded state,
    // not the previous workflow's).
    // Re-loading identical data must still stamp the clean baseline. Otherwise
    // the effect dependencies do not change and the first subsequent edit is
    // mistaken for hydration and silently skipped.
    setSyncRevision(revision => revision + 1);
    lastSavedSerialised.current = "";
    savedOnce.current = false;
    baseUpdatedTs.current = updatedTs;
    updateStatus("idle");
  }, []);

  const markConflict = useCallback(
    (detail: WorkflowConflictDetail) => {
      updateStatus("conflict");
      optsRef.current.onConflict?.(detail);
    },
    [],
  );

  return { status, save: doSave, resync, markConflict };
}
