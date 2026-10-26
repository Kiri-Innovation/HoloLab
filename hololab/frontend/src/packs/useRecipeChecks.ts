import { useEffect, useState } from 'react';
import { semanticGraph, stable } from '../canvas/analysisInputs';
import type { WorkflowGraph } from '../wire';
import type { RecipeCheck } from './packManagerModel';

export async function checkRecipe(graph: WorkflowGraph, signal?: AbortSignal): Promise<RecipeCheck> {
  const response = await fetch('/api/recipes/check', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({graph}), signal});
  if (!response.ok) throw new Error('recipe_check_failed');
  return response.json();
}
/** The canvas keeps checking the same graph, without mounting any diagnostic UI. */
export function useRecipeChecks(graph: WorkflowGraph, enabled = true) {
  const [result, setResult] = useState<RecipeCheck | null>(null);
  const [failed, setFailed] = useState(false);
  const key = stable(semanticGraph(graph));
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setFailed(false);
      void checkRecipe(graph, controller.signal).then(data => {
        if (!controller.signal.aborted) setResult(data);
      }).catch(() => { if (!controller.signal.aborted) setFailed(true); });
    }, 250);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [key, enabled]);
  return {result, failed};
}
