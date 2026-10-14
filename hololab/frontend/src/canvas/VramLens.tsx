import { useEffect, useState } from "react";
import type { GpuMetricSample } from "../wire";
import { cancelJob } from "../api";
import { TerminateProcessDialog } from "./TerminateProcessDialog";
import { ProcessMenu } from "./ProcessMenu";
import { JobLogModal } from "./JobLogModal";
import "./resourceLens.css";

export type ResourceMetric = "vram"; // Future lenses can extend this discriminated key.
interface VramProcess {
  gpu_uuid: string; pid: number; name: string; used_mb: number | null;
  termination_token?: string | null;
  cmdline?: string | null; cwd?: string | null; user?: string | null;
  started_ts?: number | null; elapsed_s?: number | null; ppid?: number | null; listen_ports?: string[] | null;
  owner: "job" | "external" | "unknown"; job_id?: string; job_name?: string;
}
interface VramData {
  ts: number; error: string | null;
  gpus: (GpuMetricSample & { uuid: string })[];
  processes: VramProcess[];
}
const gib = (mb: number | null | undefined) => mb == null ? "—" : (mb / 1024).toFixed(1);

export function VramLens({ nodeId, latest, onBack }: {
  nodeId: string; latest: GpuMetricSample | null; onBack: () => void;
}) {
  const [data, setData] = useState<VramData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const [terminating, setTerminating] = useState<VramProcess | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [stoppingJob, setStoppingJob] = useState<string | null>(null);
  const [preferSnapshot, setPreferSnapshot] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 9000);
    let disposed = false;
    setLoading(true); setError(null);
    fetch(`/api/nodes/${encodeURIComponent(nodeId)}/resources/vram`, { signal: controller.signal })
      .then(async r => { if (!r.ok) throw new Error(r.status === 404 ? "Node offline or lens endpoint unavailable" : r.status === 504 ? "Node did not reply; it may need upgrading" : `Request failed (${r.status})`); return r.json() as Promise<VramData>; })
      .then(value => { if (!disposed) setData(value); })
      .catch(e => { if (!disposed) { setData(null); setError(e.name === "AbortError" ? "Request timed out" : e.message); } })
      .finally(() => { clearTimeout(timeout); if (!disposed) setLoading(false); });
    return () => { disposed = true; clearTimeout(timeout); controller.abort(); };
  }, [nodeId, refresh]);
  useEffect(() => {
    const escape = (e: KeyboardEvent) => { if (e.key === "Escape" && !jobId && !terminating) { e.stopPropagation(); onBack(); } };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [onBack, jobId, terminating]);
  const gpu = data?.gpus.find(g => g.index === (latest?.index ?? 0));
  const processes = data?.processes.filter(p => gpu && p.gpu_uuid === gpu.uuid).sort((a,b) => (b.used_mb ?? -1) - (a.used_mb ?? -1)) ?? [];
  const total = preferSnapshot ? gpu : latest ?? gpu;
  const stopJob = async (id: string) => {
    setStoppingJob(id); setActionMessage(null);
    try { await cancelJob(id); setActionMessage("Task cancellation requested"); setRefresh(n => n + 1); }
    catch (e) { setActionMessage(e instanceof Error ? e.message : "Task cancellation failed"); }
    finally { setStoppingJob(null); }
  };
  return <section className="hl-resource-lens" aria-label="VRAM resource lens" aria-busy={loading}>
    <header><button onClick={onBack}>← All compute nodes</button><strong>VRAM · GPU {latest?.index ?? 0}</strong><button disabled={loading} onClick={() => setRefresh(n => n + 1)}>Refresh</button></header>
    <p><strong>{gib(total?.mem_used_mb)} / {gib(total?.mem_total_mb)} GiB</strong> <span className="lens-muted">{preferSnapshot ? "Process snapshot total" : "Live Pulse total"}</span></p>
    {actionMessage && <p role="status">{actionMessage}</p>}
    {loading && <p role="status">Reading GPU processes…</p>}
    {(error || data?.error) && <p role="alert">{error || data?.error}</p>}
    {data && !error && <>
      <p className="lens-muted">Process snapshot · {new Date(data.ts * 1000).toLocaleTimeString()}</p>
      {processes.map(p => <div className="hl-resource-process-entry" key={`${p.gpu_uuid}:${p.pid}`}>
        <div className="hl-resource-process">
        <div>{p.owner === "job" && p.job_id ? <button className="lens-job" onClick={() => setJobId(p.job_id!)}>{p.job_name ?? p.job_id}</button> : <strong>{p.name}</strong>}
          <div className="lens-muted">{p.owner === "job" ? "HoloLab Job" : p.owner === "external" ? "External · 非 HoloLab" : "Ownership unknown · 归属未知"} · PID {p.pid}</div>
        </div><div className="lens-process-actions"><span>{gib(p.used_mb)} GiB</span>
          {p.owner === "external" && <ProcessMenu process={{...p, nodeId, sampledAt: data.ts}} canTerminate={!!p.cmdline && !!p.termination_token && !loading} onTerminate={() => setTerminating(p)} onMessage={setActionMessage} />}
          {p.owner === "job" && p.job_id && <button disabled={stoppingJob != null} onClick={() => void stopJob(p.job_id!)}>停止任务</button>}
        </div></div>
        <details className="hl-process-details">
          <summary aria-label={`Details for ${p.name}, PID ${p.pid}`}>Details · 详情</summary>
          <div className="lens-muted">Command line</div>
          <code className="hl-process-command" title={p.cmdline ?? undefined}>{p.cmdline ?? "Unavailable"}</code>
          <dl>
            <dt>Working directory</dt><dd>{p.cwd ?? "Unavailable"}</dd>
            <dt>User</dt><dd>{p.user ?? "Unavailable"}</dd>
            <dt>Started</dt><dd>{p.started_ts == null ? "Unavailable" : new Date(p.started_ts * 1000).toLocaleString()}</dd>
            <dt>Elapsed</dt><dd>{p.elapsed_s == null ? "Unavailable" : `${Math.floor(p.elapsed_s / 3600)}h ${Math.floor(p.elapsed_s % 3600 / 60)}m ${Math.floor(p.elapsed_s % 60)}s`}</dd>
            <dt>TCP listeners</dt><dd>{p.listen_ports == null ? "Unavailable" : p.listen_ports.join(", ") || "None observed"}</dd>
            <dt>Parent PID</dt><dd>{p.ppid ?? "Unavailable"}</dd>
          </dl>
        </details>
      </div>)}
      {!loading && !data.error && processes.length === 0 && <p>No compute processes reported for this GPU.</p>}
    </>}
    <p className="lens-muted">Compute processes only. Driver/graphics allocations and sampling times can make the sum differ from the Pulse total. Refresh to update; no background polling.</p>
    {terminating && <TerminateProcessDialog nodeId={nodeId} target={terminating} onClose={() => setTerminating(null)} onSuccess={message => {
      setTerminating(null); setActionMessage(message); setPreferSnapshot(true); setData(null); setRefresh(n => n + 1);
    }} />}
    <JobLogModal jobId={jobId} onClose={() => setJobId(null)} />
  </section>;
}
