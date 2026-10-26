import "./workflowEvidence.css";
import { useEffect, useState } from "react";
import type { WorkflowGraph } from "../wire";
import { semanticGraph, stable } from "./analysisInputs";

export function RecipeProblems({graph, onLocate}: {graph: WorkflowGraph; onLocate?: (id: string) => void}) {
  const [issues, setIssues] = useState<{node_id: string; message: string; code?: string}[]>([]);
  const key = stable(semanticGraph(graph));
  useEffect(() => {
    const abort = new AbortController();
    const timer = setTimeout(() => {
      void fetch("/api/recipes/check", {method:"POST", headers:{"Content-Type":"application/json"},body:JSON.stringify({graph}),signal:abort.signal})
        .then(async r => { if (!r.ok) throw Error(await r.text()); return r.json(); })
        .then(data => { if (abort.signal.aborted) return; setIssues([...data.issues,
          ...Object.entries(data.environment ?? {}).flatMap(([node_id, raw]) => {
            const report = raw as {issues?: {actual?: string}[]};
            return (report.issues ?? []).map(i => ({node_id, message:i.actual ?? "环境未校验"}));
          })]); })
        .catch(() => { if (!abort.signal.aborted) setIssues([{node_id:"配方",message:"检查未完成，请检查连接后重试。",code:"check_failed"}]); });
    }, 250);
    return () => { clearTimeout(timer); abort.abort(); };
  }, [key]);
  return <details className="hl-evidence-panel hl-recipe-problems" open={issues.some(i => /未发现|未提供|不匹配|未声明 \(/.test(i.message))}>
    <summary>配方检查 · 保存成功 ≠ 可运行（{issues.length}）</summary>
    <p>检查结果来自当前草稿。选择节点可在 Inspector 中处理参数或依赖；保存成功不表示可运行。</p>
    {issues.map((i, index) => <div className="hl-evidence-section hl-evidence-actions" key={index}>
      {onLocate && graph.nodes.some(n => n.id === i.node_id)
        ? <button onClick={() => onLocate(i.node_id)} aria-label={`定位节点 ${i.node_id}`}>{i.node_id} ↗</button> : <strong>{i.node_id}</strong>}
      <span>{i.code === "resolution" ? "变量或参数尚未解析，请检查声明、绑定与本地值。"
        : i.code === "missing_package" ? "包未发现，请确认已安装并连接计算节点。"
        : i.code === "identity_unverified" ? "本地发布身份未记录，执行前需要校验。"
        : i.code === "missing_parameter" || i.code === "environment" || i.code === "check_failed" ? i.message
        : "本地能力需要检查，请查看能力面板中的要求与处理方式。"}</span>
    </div>)}
  </details>;
}
