import { EvidenceValue } from "./EvidenceValue";
import { createContext, useEffect, useState } from "react";
import type { WorkflowGraph } from "../wire";

export const ENVIRONMENT_BOUNDARY = "本地能力检查只覆盖已声明且在可探测范围内的项；通过不代表一定能运行。环境未冻结、未被代码身份覆盖。";
export interface EnvironmentReport {
  status: "undeclared" | "satisfied" | "missing" | "unknown";
  source?: string;
  node_id?: string;
  checked_at?: number | null;
  issues: { item: string; required: unknown; actual: unknown; remedy: string }[];
  requires?: Record<string, unknown> | null;
}
export const CapabilityContext = createContext<Record<string, EnvironmentReport>>({});
export function environmentLabel(report?: EnvironmentReport) {
  if (!report) return "环境未校验";
  return ({ undeclared: "环境未声明", satisfied: "已声明的可探测项满足", missing: "环境/硬件缺失", unknown: "环境无法确认" })[report.status];
}

export function useCapabilityChecks(graph: WorkflowGraph, revision: string, enabled: boolean) {
  const [reports, setReports] = useState<Record<string, EnvironmentReport>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  // Moving cards/editing parameters does not change an environment contract.
  const query = JSON.stringify(capabilityGraph(graph));
  useEffect(() => {
    if (!enabled) { setReports({}); setLoading(false); setError(""); return; }
    const controller = new AbortController();
    setReports({}); setLoading(true); setError("");
    const timer = window.setTimeout(() => {
      void fetch("/api/capabilities/check", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ graph: JSON.parse(query), fresh: refresh > 0 }), signal: controller.signal,
      }).then(async response => {
        if (!response.ok) throw new Error(await response.text());
        return response.json() as Promise<{ nodes: Record<string, EnvironmentReport> }>;
      }).then(data => { if (!controller.signal.aborted) setReports(data.nodes ?? {}); })
        .catch(reason => { if (!controller.signal.aborted) setError(String(reason)); })
        .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }, 400);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [query, revision, refresh, enabled]);
  useEffect(() => {
    const focus = () => setRefresh(n => n + 1);
    window.addEventListener("focus", focus);
    return () => window.removeEventListener("focus", focus);
  }, []);
  return { reports, loading, error, refresh: () => setRefresh(n => n + 1) };
}

export function capabilityGraph(graph: WorkflowGraph) {
  return { nodes: graph.nodes.filter(n => (n.kind ?? "algorithm") === "algorithm").map(n => ({
    id: n.id, kind: "algorithm", algorithm_name: n.algorithm_name,
    algorithm_version: n.algorithm_version, assigned_node_id: n.assigned_node_id,
  })), edges: [] };
}

export function EnvironmentEvidence({ report, showBoundary = true, headingLabel }: { report?: EnvironmentReport | null; showBoundary?: boolean; headingLabel?: string }) {
  return <div>
    <div className="hl-capability-heading"><strong>{headingLabel ?? environmentLabel(report ?? undefined)}</strong>
      {report?.checked_at ? <time>检查时间 {new Date(report.checked_at * 1000).toLocaleString()}</time> : null}</div>
    {report?.status === "undeclared" && <p>包作者未声明环境要求。这是信息缺失，不会因未声明而阻止运行；实际环境是否满足尚未确认。</p>}
    {report?.issues?.map((issue, i) => <section key={i} className="hl-capability-issue">
      <strong>{issue.item}</strong><dl className="hl-evidence-rows">
        <div><dt>要求</dt><dd><EvidenceValue value={issue.required} /></dd></div>
        <div><dt>当前</dt><dd><EvidenceValue value={issue.actual} /></dd></div>
        <div><dt>处理方式</dt><dd>{issue.remedy || "请检查计算节点后重新检查。"}</dd></div>
      </dl>
    </section>)}
    {showBoundary && <p className="hl-evidence-muted">{ENVIRONMENT_BOUNDARY}</p>}
  </div>;
}

export function EnvironmentChecks({ checks }: { checks: ReturnType<typeof useCapabilityChecks> }) {
  const reports = Object.values(checks.reports);
  const missing = reports.filter(r => r.status === "missing").length;
  const unknown = reports.filter(r => r.status === "unknown").length;
  const undeclared = reports.filter(r => r.status === "undeclared").length;
  return <details className="hl-evidence-panel hl-capability-panel" data-attention={missing + unknown > 0}>
    <summary>本地能力检查 · {checks.loading ? "检查中…" : checks.error ? "未能完成检查" : reports.length
      ? `${missing} 项缺失 · ${unknown} 项无法确认 · ${undeclared} 项未声明` : "暂无检查结果"}</summary>
    <div className="hl-evidence-actions"><button disabled={checks.loading} onClick={checks.refresh}>重新检查</button></div>
    {checks.error && <p role="alert">环境未校验，请检查连接后重试。</p>}
    <p>{ENVIRONMENT_BOUNDARY}</p>
    {Object.entries(checks.reports).map(([id, report]) => <details className="hl-evidence-section" key={id} open={report.status === "missing" || report.status === "unknown"}>
      <summary>{id} · {environmentLabel(report)}</summary>
      <p>{report.source ?? "来源未记录"} · 计算节点 {report.node_id ?? "未选择"}</p>
      <EnvironmentEvidence report={report} showBoundary={false} />
    </details>)}
  </details>;
}
