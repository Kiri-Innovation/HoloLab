import { useState } from "react";
import type { JobDetail } from "../wire";

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
      setRefreshStatus("已刷新，供以后提交使用；本次运行的基线记录保持不变。");
    } catch (error) { setRefreshStatus(String(error)); }
    finally { setRefreshing(false); }
  }
  const identity = detail.code_identity;
  const cold = identity?.cold as { baseline_at?: number; sha256?: string } | undefined;
  const hot = identity?.hot as { sha256?: string } | undefined;
  const ignored = identity?.ignored as { files: number; links: number; bytes: number; top_level_names: string[] } | undefined;
  const git = identity?.git as { dirty: boolean | null; commit?: string } | undefined;
  return <details style={{ padding: "8px 16px", flexShrink: 0, maxHeight: 240, overflow: "auto" }}>
    <summary>声明范围身份{identity ? "" : " · 历史运行 / 旧节点未记录"}</summary>
    {identity ? <>
      <div style={{ overflowWrap: "anywhere" }}>热代码 SHA-256: {hot?.sha256}</div>
      <div>冷代码: 未校验 · 基线时间: {cold?.baseline_at
        ? new Date(cold.baseline_at * 1000).toLocaleString() : "无冷代码"}</div>
      {ignored && <div>忽略: {ignored.files} 文件 / {ignored.links} 链接 / {ignored.bytes} 字节 · {ignored.top_level_names.join(", ") || "无"}</div>}
      <div>Git: {git?.dirty === true ? "含未提交改动" : git?.dirty === false ? "干净" : "非 Git / 未知"} · {git?.commit}</div>
      {cold?.baseline_at && detail.node_id && <button disabled={refreshing} onClick={() => void rebaseline()}>重新基线（用于以后提交）</button>}
      {refreshStatus && <div role="status">{refreshStatus}</div>}
      <p>只在启动前校验声明的热代码。运行期间的修改及未覆盖来源不受此校验保证。</p>
      <details><summary>完整记录（忽略规模 / Git 状态 / 未覆盖来源）</summary>
        <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(identity, null, 2)}</pre>
      </details>
    </> : <p>此运行没有提交时的内容身份，无法进行历史代码校验。</p>}
  </details>;
}
