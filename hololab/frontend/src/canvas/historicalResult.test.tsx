import { expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { ReactFlowProvider } from '@xyflow/react';
import type { ComponentProps } from 'react';
import { AlgorithmNode } from './AlgorithmNode';
import { CanvasContext, useCanvasContext } from './CanvasContext';
import { aggregateJobsToRuntime } from './nodeRuntime';
import type { CanvasJob } from './executionAnalysis';

vi.mock('./PinnedParameters', () => ({ PinnedParameters: () => null }));
vi.mock('./NodeViewerFrame', () => ({ NodeViewerFrame: () => null }));

const evidence = (snapshot_id: string, historical_only?: boolean): CanvasJob => ({
  job_id: 'retained-job', graph_node_id: 'n', state: 'done', created_ts: 1,
  updated_ts: 2, snapshot_id, historical_only,
} as CanvasJob);
function Card({ job, dispatch, readOnly = false }: { job: CanvasJob; dispatch: string; readOnly?: boolean }) {
  const defaults = useCanvasContext();
  const props: ComponentProps<typeof AlgorithmNode> = { id: 'n', type: 'algorithm',
    selected: false, dragging: false, draggable: true, selectable: true,
    deletable: true, zIndex: 0, isConnectable: true, positionAbsoluteX: 0, positionAbsoluteY: 0, data: {
    pack: { name: 'builtin/regroup', version: '1', inputs: {}, outputs: {}, params: {} },
    runtime: aggregateJobsToRuntime([job]).n, readOnly,
  } };
  return <CanvasContext.Provider value={{ ...defaults, dispatch_snapshot_id: dispatch }}>
    <ReactFlowProvider><AlgorithmNode {...props} /></ReactFlowProvider>
  </CanvasContext.Provider>;
}
const marked = (job: CanvasJob, dispatch: string, readOnly = false) =>
  renderToStaticMarkup(<Card job={job} dispatch={dispatch} readOnly={readOnly} />)
    .includes('data-hl-historical-branch-result');

it('keeps a retained result current across both orders of snapshot/analysis arrival', () => {
  // Local dispatch: UI id B arrives before analysis B. External dispatch:
  // analysis B arrives while this tab still has A. Old id comparison fails both.
  expect(marked(evidence('A', false), 'A')).toBe(false);
  expect(marked(evidence('A', false), 'B')).toBe(false);
  expect(marked(evidence('B', false), 'A')).toBe(false);
  expect(marked(evidence('B', false), 'B')).toBe(false);
});
it('preserves genuine historical evidence even when the local dispatch id is stale', () => {
  expect(marked(evidence('branch', true), 'current')).toBe(true);
  expect(marked(evidence('branch', true), 'branch')).toBe(true);
  expect(marked(evidence('branch', true), 'current', true)).toBe(false);
});
it('does not infer branch membership when an older gateway omits evidence', () => {
  expect(marked(evidence('A'), 'B')).toBe(false);
});
