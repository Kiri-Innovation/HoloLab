import { useEffect, useState } from "react";
import type { WorkflowGraph } from "../wire";
import { semanticGraph, stable } from "./analysisInputs";

export function RecipeProblems({graph}: {graph: WorkflowGraph}) {
  const [issues, setIssues] = useState<{node_id: string; message: string}[]>([]);
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
        .catch(e => { if (!abort.signal.aborted) setIssues([{node_id:"配方",message:String(e)}]); });
    }, 250);
    return () => { clearTimeout(timer); abort.abort(); };
  }, [key]);
  return <details style={{padding: "4px 12px"}} open={issues.some(i => /未发现|未提供|不匹配|未声明 \(/.test(i.message))}>
    <summary>配方检查 · 保存成功 ≠ 可运行（{issues.length}）</summary>
    {issues.map((i, index) => <div key={index}>{i.node_id}：{i.message}</div>)}
  </details>;
}
