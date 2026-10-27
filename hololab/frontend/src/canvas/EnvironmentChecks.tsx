import { EvidenceValue } from "./EvidenceValue";
import { createContext, useEffect, useRef, useState } from "react";
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

const CAPABILITY_CACHE_PREFIX = "hololab.capability-reports.v1:";
const CAPABILITY_CACHE_TTL_MS = 10 * 60 * 1000;
type CachedCapabilityReports = { savedAt: number; nodes: Record<string, EnvironmentReport> };

// A cache key represents the environment contract, rather than the canvas
// layout.  Sorting makes it stable when cards have merely been rearranged.
export function capabilityCacheKey(graph: WorkflowGraph) {
  const identity = capabilityGraph(graph).nodes.map(node => [
    node.id, node.algorithm_name, node.algorithm_version, node.assigned_node_id ?? null,
  ]).sort((a, b) => String(a[0]).localeCompare(String(b[0])));
  let hash = 0x811c9dc5;
  for (const char of JSON.stringify(identity)) {
    hash ^= char.charCodeAt(0);
    hash = Math.imul(hash, 0x01000193);
  }
  return `${CAPABILITY_CACHE_PREFIX}${(hash >>> 0).toString(36)}`;
}

// Capability responses include implementation details such as the remote conda
// `prefix`.  Session storage is deliberately limited to display-safe evidence.
// In particular, never persist arbitrary issue values: error strings can embed
// paths too.
export function cacheableReports(nodes: Record<string, EnvironmentReport>): Record<string, EnvironmentReport> {
  const statuses = new Set<EnvironmentReport["status"]>(["undeclared", "satisfied", "missing", "unknown"]);
  return Object.fromEntries(Object.entries(nodes).flatMap(([id, report]) => {
    if (!statuses.has(report.status)) return [];
    return [[id, {
      status: report.status,
      source: typeof report.source === "string" ? report.source : undefined,
      node_id: typeof report.node_id === "string" ? report.node_id : undefined,
      checked_at: typeof report.checked_at === "number" ? report.checked_at : null,
      issues: [],
    }]];
  }));
}

function readCachedReports(key: string): CachedCapabilityReports | null {
  if (typeof window === "undefined") return null;
  try {
    const value: unknown = JSON.parse(window.sessionStorage.getItem(key) ?? "null");
    if (!value || typeof value !== "object") return null;
    const cached = value as CachedCapabilityReports;
    if (typeof cached.savedAt !== "number" || Date.now() - cached.savedAt > CAPABILITY_CACHE_TTL_MS
      || !cached.nodes || typeof cached.nodes !== "object") return null;
    return { savedAt: cached.savedAt, nodes: cacheableReports(cached.nodes) };
  } catch {
    return null;
  }
}

function cacheReports(key: string, nodes: Record<string, EnvironmentReport>) {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(key, JSON.stringify({ savedAt: Date.now(), nodes: cacheableReports(nodes) }));
  } catch {
    // Storage can be unavailable (for example, in private browsing); checking still works.
  }
}

function reportsEqual(a: Record<string, EnvironmentReport>, b: Record<string, EnvironmentReport>) {
  return JSON.stringify(cacheableReports(a)) === JSON.stringify(cacheableReports(b));
}

export function environmentLabel(report?: EnvironmentReport) {
  if (!report) return "环境未校验";
  return ({ undeclared: "环境未声明", satisfied: "已声明的可探测项满足", missing: "环境/硬件缺失", unknown: "环境无法确认" })[report.status];
}

export function useCapabilityChecks(graph: WorkflowGraph, revision: string, enabled: boolean) {
  const cacheKey = capabilityCacheKey(graph);
  const cached = readCachedReports(cacheKey);
  const [current, setCurrent] = useState<{ key: string; reports: Record<string, EnvironmentReport> }>({ key: "", reports: {} });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [reload, setReload] = useState(0);
  const forceFresh = useRef(false);
  // Moving cards/editing parameters does not change an environment contract.
  const query = JSON.stringify(capabilityGraph(graph));
  const reports = current.key === cacheKey ? current.reports : cached?.nodes ?? {};
  const hasReports = Object.keys(reports).length > 0;
  useEffect(() => {
    if (!enabled) { setCurrent({ key: cacheKey, reports: {} }); setLoading(false); setError(""); return; }
    const fresh = forceFresh.current;
    forceFresh.current = false;
    const controller = new AbortController();
    // Retain a still-valid result while the verification request runs.  That is
    // what prevents a reload from briefly replacing evidence with "检查中".
    if (!cached) setCurrent({ key: cacheKey, reports: {} });
    setLoading(true); setError("");
    const timer = window.setTimeout(() => {
      void fetch("/api/capabilities/check", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ graph: JSON.parse(query), fresh }), signal: controller.signal,
      }).then(async response => {
        if (!response.ok) throw new Error(await response.text());
        return response.json() as Promise<{ nodes: Record<string, EnvironmentReport> }>;
      }).then(data => {
        if (controller.signal.aborted) return;
        const next = data.nodes ?? {};
        // A matching cached result is already on screen; avoid a needless
        // rerender.  A changed result replaces it immediately.
        if (!cached || !reportsEqual(cached.nodes, next)) setCurrent({ key: cacheKey, reports: next });
        cacheReports(cacheKey, next);
      })
        .catch(reason => { if (!controller.signal.aborted) setError(String(reason)); })
        .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }, 400);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [query, revision, refresh, reload, enabled, cacheKey]);
  useEffect(() => {
    const focus = () => setReload(n => n + 1);
    window.addEventListener("focus", focus);
    return () => window.removeEventListener("focus", focus);
  }, []);
  return { reports, loading: loading && !hasReports, error, refresh: () => { forceFresh.current = true; setRefresh(n => n + 1); } };
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
