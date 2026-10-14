import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

export interface TerminateTarget { pid: number; name: string; cmdline?: string | null; termination_token?: string | null; used_mb: number | null; }
export function TerminateProcessDialog({ nodeId, target, usageLabel, onClose, onSuccess }: {
  usageLabel?: string; nodeId: string; target: TerminateTarget; onClose: () => void; onSuccess: (message: string) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);
  useEffect(() => { const el = dialog.current!; el.showModal(); return () => el.close(); }, []);
  const terminate = async () => {
    if (busy || submitted || !target.cmdline || !target.termination_token) return;
    setBusy(true); setSubmitted(true); setError(null);
    const controller = new AbortController(); const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/resources/processes/terminate`, {
        method: "POST", headers: {"Content-Type": "application/json"}, signal: controller.signal,
        body: JSON.stringify({pid: target.pid, token: target.termination_token}),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(response.status === 504 ? "Result unknown: node timed out. Refresh before taking any further action." : result.detail ?? `Request failed (${response.status})`);
      if (result.status !== "terminated" && result.status !== "force-killed") throw new Error(result.reason ?? "Termination failed");
      onSuccess(`PID ${target.pid}: ${result.status === "terminated" ? "terminated (SIGTERM)" : "force-killed (SIGKILL)"}`);
    } catch (e) {
      setError(e instanceof Error && e.name === "AbortError" ? "Result unknown: request timed out. Close and refresh to check the process." : e instanceof Error ? e.message : String(e));
    } finally { clearTimeout(timeout); setBusy(false); }
  };
  return createPortal(<dialog ref={dialog} className="hl-terminate-dialog" aria-labelledby="terminate-title" aria-describedby="terminate-warning" onCancel={e => {e.preventDefault(); if (!busy) onClose();}}>
    <h3 id="terminate-title">结束进程 · Terminate process</h3>
    <p>External · 非 HoloLab · PID {target.pid} · {usageLabel ?? (target.used_mb == null ? "—" : `${(target.used_mb / 1024).toFixed(1)} GiB`)}</p>
    <p id="terminate-warning">This stops the process. SIGTERM is sent first; after 3 seconds, SIGKILL is used if it is still running.</p>
    <pre className="hl-terminate-command">{target.cmdline ?? "Command line unavailable — refresh the lens before confirming."}</pre>
    {error && <p role="alert">{error}</p>}
    {busy && <p role="status">Waiting for process exit…</p>}
    <footer><button autoFocus disabled={busy} onClick={onClose}>{submitted ? "Close" : "Cancel"}</button><button className="lens-danger" disabled={busy || submitted || !target.cmdline || !target.termination_token} onClick={terminate}>结束进程</button></footer>
  </dialog>, document.body);
}
