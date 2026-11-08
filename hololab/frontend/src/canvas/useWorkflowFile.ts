import { useCallback, useEffect, useRef, useState } from "react";
import { applyOps, diffDocument, type DocumentState } from "./workflowPatch";
import type { SaveStatus } from "./useDraftAutosave";
import { newId } from "../newId";

export function useWorkflowFile(opts: {
  id: string | null;
  snapshot: string;
  enabled: boolean;
  adopt: (d: DocumentState) => void;
  missing: () => void;
  conflict: () => void;
}) {
  const options = useRef(opts);
  options.current = opts;
  const [status, setStatus] = useState<SaveStatus>("idle");
  const [error, setError] = useState("");
  const [syncTick, bumpSync] = useState(0);
  const syncing = useRef(false);
  const state = useRef<{
    base: DocumentState | null;
    rev: number;
    ws: WebSocket | null;
    pending: { id: string; document: DocumentState } | null;
    baselineNext: boolean;
    saved: string;
    disposed: boolean;
  }>({
    base: null,
    rev: 0,
    ws: null,
    pending: null,
    baselineNext: false,
    saved: "",
    disposed: false,
  });
  const waiters = useRef<
    Array<{ resolve: () => void; reject: (e: Error) => void }>
  >([]);
  const flush = useRef<() => void>(() => {});
  const fail = useCallback((message: string) => {
    setError(message);
    setStatus("error");
    waiters.current.splice(0).forEach((w) => w.reject(Error(message)));
  }, []);
  flush.current = () => {
    const s = state.current,
      o = options.current;
    if (
      !s.base ||
      !o.snapshot ||
      !o.enabled ||
      s.baselineNext ||
      s.pending ||
      syncing.current
    )
      return;
    const current = JSON.parse(o.snapshot) as DocumentState;
    const ops = diffDocument(s.base, current);
    if (!ops.length) return;
    if (s.ws?.readyState !== WebSocket.OPEN) {
      setStatus("offline");
      return;
    }
    const id = newId();
    s.pending = { id, document: current };
    setError("");
    setStatus("saving");
    s.ws.send(
      JSON.stringify({
        kind: "workflow_edit",
        payload: { id: o.id, base_rev: s.rev, ops, request_id: id },
      }),
    );
  };
  const load = useCallback((document: DocumentState & { rev?: number }) => {
    const s = state.current;
    s.base = document;
    s.rev = document.rev ?? 0;
    s.pending = null;
    const current = options.current.snapshot
      ? JSON.parse(options.current.snapshot)
      : null;
    s.baselineNext = !current || diffDocument(current, document).length > 0;
    s.saved = current && !s.baselineNext ? options.current.snapshot : "";
    setError("");
    setStatus("idle");
  }, []);
  useEffect(() => {
    if (!opts.id) return;
    const s = state.current;
    s.disposed = false;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const connect = () => {
      syncing.current = true;
      const ws = new WebSocket(
        `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/frontend`,
      );
      s.ws = ws;
      ws.onopen = () => {
        // Reconnect always obtains the new gateway's revision and rebases
        // unsent local fields; revisions are scoped to one gateway lifetime.
        if (s.pending) s.pending = null;
        fetch(`/api/workflows/${encodeURIComponent(options.current.id!)}`)
          .then((r) => r.json())
          .then((d) => {
            if (disposed || s.ws !== ws || !d.graph) return;
            const local =
              s.base && options.current.snapshot
                ? diffDocument(s.base, JSON.parse(options.current.snapshot))
                : [];
            s.base = { name: d.name, graph: d.graph };
            s.rev = d.rev ?? 0;
            syncing.current = false;
            if (d.missing) {
              options.current.missing();
              return;
            }
            if (local.length) {
              const merged = applyOps(s.base, local);
              options.current.adopt(merged);
            } else if (
              options.current.snapshot &&
              diffDocument(JSON.parse(options.current.snapshot), s.base).length
            ) {
              s.baselineNext = true;
              options.current.adopt(s.base);
            } else if (!local.length && !d.saving) {
              s.saved = options.current.snapshot;
              setStatus("saved");
              waiters.current.splice(0).forEach((w) => w.resolve());
            }
            bumpSync((n) => n + 1);
          })
          .catch((e) => fail(String(e)));
      };
      ws.onmessage = (event) => {
        const frame = JSON.parse(event.data);
        if (frame.kind !== "workflow_changed") return;
        const p = frame.payload;
        if (p.id !== options.current.id) return;
        if (p.missing) {
          setStatus("error");
          options.current.missing();
          fail(p.error ?? "workflow file missing");
          return;
        }
        if (p.error && !p.rejected) {
          if (p.error.includes("id does not match")) options.current.missing();
          fail(p.error);
          return;
        }
        if (p.saved) {
          if (p.rev === s.rev && !s.pending) {
            const current = options.current.snapshot
              ? JSON.parse(options.current.snapshot)
              : null;
            if (current && s.base && !diffDocument(s.base, current).length) {
              s.saved = options.current.snapshot;
              setStatus("saved");
              waiters.current.splice(0).forEach((w) => w.resolve());
            } else flush.current();
          }
          return;
        }
        const own = !!s.pending && p.request_id === s.pending.id;
        if (p.rejected && !own) return;
        if (
          p.rejected &&
          own &&
          p.error !== "base_rev is behind current revision"
        ) {
          s.pending = null;
          fail(p.error ?? "patch rejected");
          return;
        }
        if (!p.graph?.nodes) return;
        setError("");
        const current = options.current.snapshot
          ? JSON.parse(options.current.snapshot)
          : s.base;
        const local =
          s.base && current
            ? diffDocument(
                own && !p.rejected ? s.pending!.document : s.base,
                current,
              )
            : [];
        s.base = { name: p.name, graph: p.graph };
        s.rev = p.rev;
        if (own) s.pending = null;
        if (p.rejected && p.error !== "base_rev is behind current revision") {
          fail(p.error ?? "patch rejected");
          return;
        }
        if (own && !p.rejected) {
          flush.current();
          return;
        }
        if (local.length) {
          options.current.conflict();
          options.current.adopt(applyOps(s.base, local));
          setStatus("saving");
        } else if (!own) {
          if (current && diffDocument(current, s.base).length) {
            s.baselineNext = true;
            options.current.adopt(s.base);
          }
          setStatus(p.request_id ? "saving" : "saved");
          s.saved =
            current && !diffDocument(current, s.base).length
              ? options.current.snapshot
              : JSON.stringify(s.base);
        }
        // For our own ACK the optimistic canvas already has this graph.
        // The next render emits any edits made while awaiting that ACK.
        if (own && !local.length) flush.current();
        bumpSync((n) => n + 1);
      };
      ws.onclose = () => {
        if (!disposed && s.ws === ws) {
          setStatus("offline");
          timer = setTimeout(connect, 1000);
        }
      };
      ws.onerror = () => ws.close();
    };
    connect();
    return () => {
      disposed = true;
      s.disposed = true;
      clearTimeout(timer);
      s.ws?.close();
    };
  }, [opts.id, fail]);
  useEffect(() => {
    const s = state.current;
    if (!opts.snapshot || !s.base) return;
    if (s.baselineNext) {
      s.base = JSON.parse(opts.snapshot);
      s.baselineNext = false;
      s.saved = opts.snapshot;
      return;
    }
    flush.current();
  }, [opts.snapshot, opts.enabled, syncTick]);
  useEffect(() => {
    const onUnload = (event: BeforeUnloadEvent) => {
      if (
        options.current.enabled &&
        state.current.base &&
        options.current.snapshot &&
        state.current.saved !== options.current.snapshot
      ) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", onUnload);
    return () => window.removeEventListener("beforeunload", onUnload);
  }, []);
  const save = useCallback(async () => {
    flush.current();
    const s = state.current;
    if (!s.pending && s.saved === options.current.snapshot) return;
    await new Promise<void>((resolve, reject) => {
      const waiter = {
        resolve: () => {
          clearTimeout(timer);
          resolve();
        },
        reject: (e: Error) => {
          clearTimeout(timer);
          reject(e);
        },
      };
      const timer = setTimeout(() => {
        waiters.current = waiters.current.filter((w) => w !== waiter);
        reject(Error("保存超时，请重试"));
      }, 30000);
      waiters.current.push(waiter);
    });
  }, []);
  return {
    status,
    error,
    save,
    load,
    discard: () => {
      state.current.pending = null;
      state.current.saved = options.current.snapshot;
    },
    savedSerialisedSnapshot: () => state.current.saved,
  };
}
