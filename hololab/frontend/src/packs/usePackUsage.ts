import {useEffect, useState} from 'react';
import type {WorkflowUsage} from './packManagerModel';

/** One reference-index request; never download or check every workflow graph. */
export function usePackUsage(key: string, revision: number, active: boolean) {
  const [result, setResult] = useState<{key: string; items: WorkflowUsage[]; loading: boolean; error: boolean}>({key, items: [], loading: true, error: false});
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    setResult({key, items: [], loading: true, error: false});
    const path = key ? `/api/packs/${encodeURIComponent(key)}/usage` : '/api/packs/usage';
    void fetch(path, {signal: controller.signal}).then(async response => {
      if (!response.ok) throw new Error('usage_failed');
      const items = await response.json();
      if (!Array.isArray(items) || items.some(item => !item || typeof item.workflow_id !== 'string' || !Array.isArray(item.graph?.nodes))) throw new Error('usage_invalid');
      if (!controller.signal.aborted) setResult({key, items, loading: false, error: false});
    }).catch(() => {
      if (!controller.signal.aborted) setResult({key, items: [], loading: false, error: true});
    });
    return () => controller.abort();
  }, [key, revision, retry, active]);
  return {...(result.key === key ? result : {items: [], loading: true, error: false}), refresh: () => setRetry(n => n + 1)};
}
