import { EvidenceValue } from "./EvidenceValue";
import { useState } from "react";
import type { JobDetail } from "../wire";
import { PublishedAsNotes } from "./PublicationStatus";
import { EnvironmentEvidence, type EnvironmentReport } from "./EnvironmentChecks";

// Capture before workflow navigation removes the query string, as NodeInspector does.
const DEBUG_EVIDENCE = typeof window !== "undefined" && new URLSearchParams(window.location.search).get("debug") === "1";

/** Historical evidence; refreshing a baseline must never rewrite this record. */
export function CodeIdentityPanel({ detail }: { detail: JobDetail }) {
  const [refreshStatus, setRefreshStatus] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  async function rebaseline() {
    if (!window.confirm("更新此计算节点的冷代码基线？以后提交将使用新基线，本次运行的冻结记录不会改变。")) return;
    setRefreshing(true);
    setRefreshStatus("正在重新基线…");
    try {
      const response = await fetch(`/api/nodes/${encodeURIComponent(detail.node_id!)}/code-baseline`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: detail.algorithm_name, version: detail.algorithm_version }),
      });
      if (!response.ok) throw new Error(await response.text());
      setRefreshStatus("基线已更新，供以后提交使用；本次运行的基线记录保持不变。");
    } catch { setRefreshStatus("基线未能更新，请检查计算节点连接后重试。"); }
    finally { setRefreshing(false); }
  }
  const identity = detail.code_identity;
  const cold = identity?.cold as { baseline_at?: number } | undefined;
  return <details className="hl-evidence-panel hl-code-identity">
    <summary>声明范围身份{identity ? "" : " · 历史运行未记录代码身份"}</summary>
    <PublishedAsNotes notes={detail.published_as} />
    <IdentityEvidence identity={identity} effectiveParams={detail.params} />
    {cold?.baseline_at && detail.node_id && <button className="hl-evidence-primary" disabled={refreshing} onClick={() => void rebaseline()}>重新基线（用于以后提交）</button>}
    {refreshStatus && <div role="status">{refreshStatus}</div>}
  </details>;
}

export function IdentityEvidence({ identity, effectiveParams }: {
  identity?: Record<string, unknown> | null;
  effectiveParams?: Record<string, unknown>;
}) {
  if (!identity) return <><p>历史运行未记录代码身份；不会用当前代码回填。</p><EnvironmentEvidence /></>;
  const cold = identity.cold as { baseline_at?: number; sha256?: string; baseline_status?: string; content_changed?: boolean } | undefined;
  const hot = identity.hot as { sha256?: string; verification?: string } | undefined;
  const manifest = identity.manifest as { sha256?: string; raw_sha256?: string } | undefined;
  const controls = identity.controls as { manifest_sha256?: string; packignore_sha256?: string } | undefined;
  const ignored = identity.ignored as { files: number; links: number; bytes: number; top_level_names: string[] } | undefined;
  const git = identity.git as { dirty: boolean | null; commit?: string; tree_sha?: string } | undefined;
  const rows = (items: [string, unknown][]) => <dl className="hl-evidence-rows">{items.map(([label, value]) =>
    <div key={label}><dt>{label}</dt><dd><EvidenceValue value={value} /></dd></div>)}</dl>;
  return <div className="hl-identity-evidence">
    <section className="hl-evidence-section"><h4>声明范围</h4>{rows([
      ["来源", identity.source ?? "未知"], ["身份格式", identity.format ?? "旧格式"], ["SHA-256", identity.sha256],
    ])}</section>
    <section className="hl-evidence-section"><h4>热代码</h4>
      <p>{hot?.verification === "verified_at_submission" ? "提交时已校验；启动前再次校验" : "旧格式记录，未记录校验方式"}</p>
      {rows([["SHA-256", hot?.sha256]])}</section>
    <section className="hl-evidence-section"><h4>冷代码</h4><p>仅记录基线，启动前未校验</p>
      {rows([["SHA-256", cold?.sha256], ["基线时间", cold?.baseline_at ? new Date(cold.baseline_at * 1000).toLocaleString() : "未记录冷基线"]])}
      {cold?.baseline_status === "updated" && <p>基线已更新{cold.content_changed === false ? "（内容未变）" : ""}</p>}</section>
    <details className="hl-evidence-section"><summary>Manifest 与忽略规则摘要</summary>{rows([
      ["Manifest 归一化 SHA-256", manifest?.sha256 ?? "旧格式未记录"],
      ["Manifest 原始字节 SHA-256", manifest?.raw_sha256 ?? controls?.manifest_sha256],
      [".packignore SHA-256", controls?.packignore_sha256 ?? "无文件"],
    ])}</details>
    <section className="hl-evidence-section"><h4>忽略规模</h4>{ignored
      ? <>{rows([["文件 / 链接", `${ignored.files} / ${ignored.links}`], ["字节", ignored.bytes], ["顶层目录或文件", ignored.top_level_names]])}</>
      : <p>未记录忽略规模</p>}</section>
    <section className="hl-evidence-section"><h4>Git 状态</h4>{rows([
      ["工作区", git?.dirty === true ? "含未提交改动" : git?.dirty === false ? "干净" : "非 Git / 未知"],
      ["Commit", git?.commit], ["Tree", git?.tree_sha],
    ])}</section>
    <details className="hl-evidence-section" open><summary>未覆盖来源</summary><EvidenceValue value={identity.uncovered ?? []} /></details>
    <details className="hl-evidence-section"><summary>未声明来源（非完整依赖发现）</summary><EvidenceValue value={identity.undeclared ?? []} /></details>
    <details className="hl-evidence-section"><summary>提交时有效参数</summary><EvidenceValue value={identity.effective_params ?? effectiveParams ?? {}} /></details>
    <details className="hl-evidence-section"><summary>当次环境检查</summary><EnvironmentEvidence report={identity.environment as EnvironmentReport | undefined} /></details>
    <p>运行期间的修改及未覆盖来源不受此校验保证。元数据发布不改变语义身份，但旧运行仍核对原始文件字节。</p>
    {DEBUG_EVIDENCE && <details><summary>完整冻结记录（调试）</summary><pre style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(identity, null, 2)}</pre></details>}
  </div>;
}
