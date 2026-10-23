import { createContext, useEffect, useState } from "react";
import type { WorkflowGraph } from "../wire";

export const ENVIRONMENT_BOUNDARY = "本地能力检查只覆盖已声明且在可探测范围内的项；通过不代表一定能运行。环境未冻结、未被代码身份覆盖。";
export interface EnvironmentReport {
  status: "undeclared" | "satisfied" | "missing" | "unknown";
  source?: string;
  node_id?: string;
  checked_at?: number | null;
  issues: { item: string; required: unknown; actual: unknown; remedy: string }[];
}
export const CapabilityContext = createContext<Record<string, EnvironmentReport>>({});
export function environmentLabel(report?: EnvironmentReport) {
  return report ? ({ undeclared: "环境未声明", satisfied: "已声明的可探测项满足", missing: "环境/硬件缺失", unknown: "环境无法确认" })[report.status] : "环境未校验";
}

export function useCapabilityChecks(graph: WorkflowGraph, revision: string, enabled: boolean) {
  const [reports, setReports] = useState<Record<string, EnvironmentReport>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  // Moving cards/editing parameters does not change an environment contract.
  const query = JSON.stringify(capabilityGraph(graph));
  useEffect(() => {
    if (!enabled) { setReports({}); return; }
    const controller = new AbortController();
    setReports({}); setLoading(true); setError("");
    const timer = window.setTimeout(() => {
      void fetch("/api/capabilities/check", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ graph: JSON.parse(query), fresh: refresh > 0 }), signal: controller.signal,
      }).then(async response => {
        if (!response.ok) throw new Error(await response.text());
        return response.json() as Promise<{ nodes: Record<string, EnvironmentReport> }>;
      }).then(data => { if (!controller.signal.aborted) setReports(data.nodes); })
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

export function EnvironmentEvidence({ report }: { report?: EnvironmentReport | null }) {
  return <div>
    <div>{environmentLabel(report ?? undefined)}{report?.checked_at ? ` · 检查时间 ${new Date(report.checked_at * 1000).toLocaleString()}` : ""}</div>
    {report?.issues?.map((issue, i) => <div key={i} style={{ marginTop: 4 }}>
      {issue.item}：要求 {JSON.stringify(issue.required)}；当前 {JSON.stringify(issue.actual)}。{issue.remedy}
    </div>)}
    <p style={{ color: "var(--text-muted)" }}>{ENVIRONMENT_BOUNDARY}</p>
  </div>;
}

export function EnvironmentChecks({ checks }: { checks: ReturnType<typeof useCapabilityChecks> }) {
  const missing = Object.values(checks.reports).filter(r => r.status === "missing" || r.status === "unknown").length;
  const undeclared = Object.values(checks.reports).filter(r => r.status === "undeclared").length;
  return <details style={{ position: "absolute", top: 42, left: 12, zIndex: 8,
    maxWidth: "min(440px, 90%)", maxHeight: "55%", overflow: "auto", background: "var(--surface-2)",
    padding: 8, border: "1px solid var(--border)", borderRadius: 6, fontSize: "var(--fs-xs)" }}>
    <summary style={{ color: missing ? "var(--danger, #d54b4b)" : "var(--text-muted)" }}>
      本地能力检查 · {checks.loading ? "检查中…" : checks.error ? "查询失败" : `${missing} 项缺失/无法确认 · ${undeclared} 项未声明`}
    </summary>
    <button disabled={checks.loading} onClick={checks.refresh}>重新检查</button>
    {checks.error && <p role="alert">环境未校验：{checks.error}</p>}
    <p>{ENVIRONMENT_BOUNDARY}</p>
    {Object.entries(checks.reports).map(([id, report]) => <details key={id} open={report.status === "missing" || report.status === "unknown"}>
      <summary>{id} · {report.source} · 节点 {report.node_id ?? "未选择"} · {environmentLabel(report)}</summary>
      <EnvironmentEvidence report={report} />
    </details>)}
  </details>;
}
