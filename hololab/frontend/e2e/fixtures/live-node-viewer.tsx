import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ReactFlowProvider } from '@xyflow/react';
import { NodePreviews, useNodePreviewSelection } from '../../src/canvas/NodePreviews';
import type { AlgorithmNodeData } from '../../src/canvas/AlgorithmNode';
import { CanvasContext, useCanvasContext } from '../../src/canvas/CanvasContext';
function Fixture() {
  const [state, setState] = useState('running');
  const [native, setNative] = useState(false);
  const defaults = useCanvasContext();
  const data: AlgorithmNodeData = {
    pack: { name: 'test/train', version: '1', inputs: {}, outputs: { state: { tags: ['folder'] } }, params: {},
      hide_default_output_previews: true,
      node_viewers: [{ id: 'diagnostics', renderer: 'iframe', default: true, entry: 'viewer.html',
        history_output: 'state', history_index: 'previews/index.json',
        live_globs: ['{{ outputs.state }}/previews/iter_*.png', '{{ outputs.state }}/save_cam/iter*_ssim.png'] }] },
    assigned_node_id: 'worker',
    runtime: { state, job_id: 'current', node_id: 'worker', workflow_id: 'workflow',
      progress: native ? { current: 800, total: 1000 } : null },
    previews: { state: { port_name: 'state', handle_id: 'old', node_id: 'worker',
      proxy_url: '/proxy/worker/old/state', storage: 'dir', tags: ['folder'], absolute_path: '' } },
  };
  const selection = useNodePreviewSelection(data);
  return <CanvasContext.Provider value={{ ...defaults, viewer_view_loaded: true }}>
    <button onClick={() => setState('done')}>Finish</button>
    <button onClick={() => setNative(true)}>Native progress</button>
    <NodePreviews data={data} selection={selection} />
  </CanvasContext.Provider>;
}
createRoot(document.getElementById('root')!).render(<ReactFlowProvider><Fixture /></ReactFlowProvider>);
