import { useEffect, useRef, useState } from 'react';
import type { WorkflowGraph } from '../wire';
import type { NodeStaleness } from './staleness';

export interface ExecutionAnalysis {
  revision: string;
  graph: WorkflowGraph;
  staleness: Record<string, NodeStaleness | null>;
  diff: { description: string }[];
  environment_params: Record<string, string[]>;
}

/** Request generations, not response arrival order, decide which result is current. */
export class AnalysisGeneration {
  private sequence = 0;
  next() { return ++this.sequence; }
  current(value: number) { return value === this.sequence; }
}

export function useExecutionAnalysis(workflow: string | null, graph: WorkflowGraph, refresh: unknown) {
  const key = JSON.stringify([workflow, graph, refresh]);
  const generation = useRef(new AnalysisGeneration());
  const [result, setResult] = useState<{ key: string; data?: ExecutionAnalysis; error?: string }>();
  useEffect(() => {
    const id = generation.current.next();
    if (!workflow) return;
    const abort = new AbortController();
    const timer = setTimeout(() => {
      void fetch(`/api/workflows/${encodeURIComponent(workflow)}/analyze`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ graph }), signal: abort.signal,
      }).then(async response => {
        const data = await response.json();
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '无法解析当前配置');
        if (!data.graph || !data.staleness || !Array.isArray(data.diff)) throw new Error('Gateway 尚未提供有效配置检查');
        if (generation.current.current(id)) setResult({ key, data });
      }).catch(error => {
        if (!abort.signal.aborted && generation.current.current(id)) setResult({ key, error: String(error.message) });
      });
    }, 200);
    return () => { clearTimeout(timer); abort.abort(); generation.current.next(); };
  // The key includes every semantic input and current run status.
  }, [key]);
  return result?.key === key ? result : undefined;
}
