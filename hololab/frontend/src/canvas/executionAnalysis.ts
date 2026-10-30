import { useEffect, useRef, useState } from 'react';
import type { SnapshotJob, WorkflowGraph } from '../wire';
import type { NodeStaleness } from './staleness';
import { analysisSignatures, type AnalysisContext } from './analysisInputs';
import { analysisMetrics } from './analysisMetrics';

export type CanvasJob = SnapshotJob & { snapshot_id: string };

export interface ExecutionAnalysis {
  canvas_jobs?: CanvasJob[];
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

export function useExecutionAnalysis(workflow: string | null, graph: WorkflowGraph, context?: AnalysisContext | null) {
  const [refresh, setRefresh] = useState(0);
  const signatures = analysisSignatures(graph, context);
  const key = JSON.stringify([workflow, signatures.edit, signatures.baseline]);
  const generation = useRef(new AnalysisGeneration());
  type Result = { workflow: string; key: string; edit: string; baseline: string; data?: ExecutionAnalysis; error?: string };
  const [result, setResult] = useState<Result>();
  const [lastSuccess, setLastSuccess] = useState<Result>();
  // Schedule after completion: slow analysis must not be aborted by a timer.
  useEffect(() => {
    if (!workflow || !result || result.key !== key) return;
    const timer = setTimeout(() => setRefresh(n => n + 1), 5000);
    return () => clearTimeout(timer);
  }, [workflow, result, key]);
  useEffect(() => { setResult(undefined); setLastSuccess(undefined); }, [workflow]);
  useEffect(() => {
    const id = generation.current.next();
    if (!workflow) return;
    const abort = new AbortController();
    let pending = false;
    const timer = setTimeout(() => {
      pending = true;
      analysisMetrics.sent++;
      void fetch(`/api/workflows/${encodeURIComponent(workflow)}/analyze`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ graph }), signal: abort.signal,
      }).then(async response => {
        const data = await response.json();
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '无法解析当前配置');
        if (!data.graph || !data.staleness || !Array.isArray(data.diff)) throw new Error('Gateway 尚未提供有效配置检查');
        if (generation.current.current(id)) {
          analysisMetrics.completed++;
          const next = {workflow,key,edit:signatures.edit,baseline:signatures.baseline,data};
          setResult(next); setLastSuccess(next);
        }
      }).catch(error => {
        if (!abort.signal.aborted && generation.current.current(id)) {
          analysisMetrics.failed++;
          setResult({workflow,key,edit:signatures.edit,baseline:signatures.baseline,error:String(error.message)});
        }
      }).finally(() => {pending=false;});
    }, 200);
    return () => { clearTimeout(timer); if(pending) analysisMetrics.cancelled++; abort.abort(); generation.current.next(); };
  // Semantic edits, baseline changes, and completed-request polling refresh evidence.
  }, [key, refresh]);
  const current = result?.workflow === workflow && result.key === key ? result : undefined;
  const previous = lastSuccess?.workflow === workflow ? lastSuccess : undefined;
  const data = current?.data ?? previous?.data;
  const status = current?.error ? 'error' : current?.data ? 'ready' : data ? 'checking' : 'unchecked';
  return {data, error:current?.error, status,
    // Evidence identity is separate from the Gateway's configuration revision.
    resultEdit: previous?.edit, resultBaseline: previous?.baseline,
    edit:signatures.edit, baseline:signatures.baseline, isCurrent:status === 'ready',
    message: status === 'error' ? `检查失败：${current?.error}${data ? '；保留上次结论' : ''}`
      : status === 'checking' ? '检查中，保留上次结论'
      : status === 'unchecked' ? '尚未检查' : undefined,
  };
}
