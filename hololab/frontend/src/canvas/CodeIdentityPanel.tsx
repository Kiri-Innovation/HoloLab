import { useState } from "react";
import type { JobDetail } from "../wire";
import { EnvironmentEvidence, type EnvironmentReport } from "./EnvironmentChecks";

/** Historical evidence; refreshing a baseline must never rewrite this record. */
export function CodeIdentityPanel({ detail }: { detail: JobDetail }) {
  const [refreshStatus, setRefreshStatus] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  async function rebaseline() {
    setRefreshing(true);
    setRefreshStatus("正在重新基线…");
    try {
      const response = await fetch(`/api/nodes/${encodeURIComponent(detail.node_id!)}/code-baseline`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: detail.algorithm_name, version: detail.algorithm_version }),
      });
      if (!response.ok) throw new Error(await response.text());
      setRefreshStatus("基线已更新，供以后提交使用；本次运行的基线记录保持不变。");
    } catch (error) { setRefreshStatus(String(error)); }
    finally { setRefreshing(false); }
  }
  const identity = detail.code_identity;
  const cold = identity?.cold as { baseline_at?: number } | undefined;
  return <details style={{ padding: "8px 16px", flexShrink: 0, maxHeight: 300, overflow: "auto" }}>
    <summary>声明范围身份{identity ? "" : " · 历史运行未记录代码身份"}</summary>
    <IdentityEvidence identity={identity} effectiveParams={detail.params} />
    {cold?.baseline_at && detail.node_id && <button disabled={refreshing} onClick={() => void rebaseline()}>重新基线（用于以后提交）</button>}
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
  return <div style={{ overflowWrap: "anywhere" }}>
    <EnvironmentEvidence report={identity.environment as EnvironmentReport | undefined} />
    <div>身份格式: {String(identity.format ?? "旧格式")} · 来源: {String(identity.source ?? "未知")}</div>
    <div>声明范围 SHA-256: {String(identity.sha256 ?? "未记录")}</div>
    <div>热代码 SHA-256: {hot?.sha256} · {hot?.verification === "verified_at_submission" ? "提交时已校验；启动前再次校验" : "旧格式记录"}</div>
    <div>Manifest 归一化 SHA-256: {manifest?.sha256 ?? "旧格式未记录"}</div>
    <div>Manifest 原始字节 SHA-256: {manifest?.raw_sha256 ?? controls?.manifest_sha256}</div>
    <div>.packignore SHA-256: {controls?.packignore_sha256 ?? "无文件"}</div>
    <div>冷代码 SHA-256: {cold?.sha256} · 仅记录基线，启动前未校验</div>
    <div>冷基线时间: {cold?.baseline_at ? new Date(cold.baseline_at * 1000).toLocaleString() : "无冷代码"}
      {cold?.baseline_status === "updated" && ` · 基线已更新${cold.content_changed === false ? "（内容未变）" : ""}`}</div>
    {ignored && <div>忽略: {ignored.files} 文件 / {ignored.links} 链接 / {ignored.bytes} 字节 · {ignored.top_level_names.join(", ") || "无"}</div>}
    <div>Git: {git?.dirty === true ? "含未提交改动" : git?.dirty === false ? "干净" : "非 Git / 未知"} · commit: {git?.commit} · tree: {git?.tree_sha}</div>
    <div>未覆盖来源: <pre style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(identity.uncovered ?? [], null, 2)}</pre></div>
    <div>未声明来源（非完整依赖发现）: <pre style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(identity.undeclared ?? [], null, 2)}</pre></div>
    <details><summary>提交时有效参数</summary><pre style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(identity.effective_params ?? effectiveParams ?? {}, null, 2)}</pre></details>
    <p>运行期间的修改及未覆盖来源不受此校验保证。元数据发布不改变语义身份，但旧运行仍核对原始文件字节。</p>
    <details><summary>完整冻结记录</summary><pre style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(identity, null, 2)}</pre></details>
  </div>;
}
