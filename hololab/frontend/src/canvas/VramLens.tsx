import { useEffect, useState } from "react";
import type { GpuMetricSample } from "../wire";
import { cancelJob } from "../api";
import { TerminateProcessDialog } from "./TerminateProcessDialog";
import { ProcessMenu } from "./ProcessMenu";
import { JobLogModal } from "./JobLogModal";
import "./resourceLens.css";

export type ResourceMetric = "vram" | "gpu" | "cpu" | "mem"; // Future lenses can extend this discriminated key.
interface VramProcess {
  gpu_uuid?: string; usage_percent?: number | null; pid: number; name: string; used_mb: number | null;
  termination_token?: string | null; termination_reason?: string | null;
  cmdline?: string | null; cwd?: string | null; user?: string | null;
  started_ts?: number | null; elapsed_s?: number | null; ppid?: number | null; listen_ports?: string[] | null;
  owner: "job" | "external" | "unknown"; job_id?: string; job_name?: string;
}
interface VramData {
  total_percent?: number; total_used_mb?: number; total_capacity_mb?: number; sample_window_s?: number;
  ts: number; error: string | null;
  gpus: (GpuMetricSample & { uuid: string })[];
  processes: VramProcess[];
}
const gib = (mb: number | null | undefined) => mb == null ? "—" : (mb / 1024).toFixed(1);

export function VramLens({ nodeId, latest, onBack, metric = "vram" }: {
  metric?: ResourceMetric; nodeId: string; latest: GpuMetricSample | null; onBack: () => void;
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
    const timeout = setTimeout(() => controller.abort(), 12000);
    let disposed = false;
    setLoading(true); setError(null);
    fetch(`/api/nodes/${encodeURIComponent(nodeId)}/resources/${metric}`, { signal: controller.signal })
      .then(async r => { if (!r.ok) throw new Error(r.status === 409 ? "Compute node needs upgrading for this resource metric" : r.status === 404 ? "Node offline or lens endpoint unavailable" : r.status === 504 ? "Node did not reply; it may need upgrading" : `Request failed (${r.status})`); return r.json() as Promise<VramData>; })
      .then(value => { if (!disposed) setData(value); })
      .catch(e => { if (!disposed) { setData(null); setError(e.name === "AbortError" ? "Request timed out" : e.message); } })
      .finally(() => { clearTimeout(timeout); if (!disposed) setLoading(false); });
    return () => { disposed = true; clearTimeout(timeout); controller.abort(); };
  }, [nodeId, metric, refresh]);
  useEffect(() => {
    const escape = (e: KeyboardEvent) => { if (e.key === "Escape" && !jobId && !terminating) { e.stopPropagation(); onBack(); } };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [onBack, jobId, terminating]);
  const gpuMetric = metric === "vram" || metric === "gpu";
  const percentMetric = metric === "cpu" || metric === "gpu";
  const usage = (p: VramProcess) => percentMetric ? p.usage_percent : p.used_mb;
  const formatUsage = (p: VramProcess) => percentMetric ? (p.usage_percent == null ? "—" : `${p.usage_percent.toFixed(1)}%`) : `${gib(p.used_mb)} GiB`;
  const gpu = data?.gpus.find(g => g.index === (latest?.index ?? 0));
  const processes = data?.processes.filter(p => !gpuMetric || (gpu && p.gpu_uuid === gpu.uuid)).sort((a,b) => (usage(b) ?? -1) - (usage(a) ?? -1)) ?? [];
  const total = preferSnapshot ? gpu : latest ?? gpu;
  const stopJob = async (id: string) => {
    setStoppingJob(id); setActionMessage(null);
    try { await cancelJob(id); setActionMessage("Task cancellation requested"); setRefresh(n => n + 1); }
    catch (e) { setActionMessage(e instanceof Error ? e.message : "Task cancellation failed"); }
    finally { setStoppingJob(null); }
  };
  return <section className="hl-resource-lens" aria-label={`${metric.toUpperCase()} resource lens`} aria-busy={loading}>
    <header><button onClick={onBack}>← All compute nodes</button><strong>{metric.toUpperCase()}{gpuMetric ? ` · GPU ${latest?.index ?? 0}` : " · Compute node"}</strong><button disabled={loading} onClick={() => setRefresh(n => n + 1)}>Refresh</button></header>
    <p><strong>{metric === "vram" ? `${gib(total?.mem_used_mb)} / ${gib(total?.mem_total_mb)} GiB` : metric === "mem" ? `${gib(data?.total_used_mb)} / ${gib(data?.total_capacity_mb)} GiB` : `${(metric === "gpu" ? gpu?.util_percent : data?.total_percent)?.toFixed(1) ?? "—"}%`}</strong> <span className="lens-muted">{metric === "vram" && !preferSnapshot ? "Live Pulse total" : "Host / device snapshot total"}</span></p>
    <p className="lens-muted">{metric === "cpu" ? `CPU: two /proc samples, approximately ${data?.sample_window_s?.toFixed(2) ?? "0.30"}s apart; 100% means all logical CPUs combined. New, exited or inaccessible processes may be omitted; process sums may differ from host totals.` : metric === "mem" ? "Memory: per-process RSS. Shared pages are counted in each process, so RSS sums can exceed physical memory. Host usage uses MemTotal − MemAvailable." : metric === "gpu" ? "GPU: nvidia-smi pmon -c 1, per-process SM utilization over the driver's sample interval. Compute processes only; unavailable values are —. Device totals and process samples may differ." : "Compute processes only. Driver/graphics allocations and sampling times can make the sum differ from the Pulse total."} Refresh to update; no background polling.</p>
    {actionMessage && <p role="status">{actionMessage}</p>}
    {loading && <p role="status">Reading resource processes…</p>}
    {(error || data?.error) && <p role="alert">{error || data?.error}</p>}
    {data && !error && <>
      <p className="lens-muted">Process snapshot · {new Date(data.ts * 1000).toLocaleTimeString()}</p>
      {processes.map(p => <div className="hl-resource-process-entry" key={`${p.gpu_uuid}:${p.pid}`}>
        <div className="hl-resource-process">
        <div>{p.owner === "job" && p.job_id ? <button className="lens-job" onClick={() => setJobId(p.job_id!)}>{p.job_name ?? p.job_id}</button> : <strong>{p.name}</strong>}
          <div className="lens-muted">{p.owner === "job" ? "HoloLab Job" : p.owner === "external" ? "External · 非 HoloLab" : "Ownership unknown · 归属未知"} · PID {p.pid}</div>
        </div><div className="lens-process-actions"><span>{formatUsage(p)}</span>
          {p.owner === "external" && <ProcessMenu process={{...p, nodeId, sampledAt: data.ts, metric}} terminationReason={p.termination_reason} canTerminate={!!p.cmdline && !!p.termination_token && !loading} onTerminate={() => setTerminating(p)} onMessage={setActionMessage} />}
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
      {!loading && !data.error && processes.length === 0 && <p>No processes reported for this resource.</p>}
    </>}

    {terminating && <TerminateProcessDialog nodeId={nodeId} target={terminating} usageLabel={formatUsage(terminating)} onClose={() => setTerminating(null)} onSuccess={message => {
      setTerminating(null); setActionMessage(message); setPreferSnapshot(true); setData(null); setRefresh(n => n + 1);
    }} />}
    <JobLogModal jobId={jobId} onClose={() => setJobId(null)} />
  </section>;
}
