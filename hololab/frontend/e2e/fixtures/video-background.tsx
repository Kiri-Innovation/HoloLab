import React from 'react';
import { createRoot } from 'react-dom/client';
import { ReactFlow, ReactFlowProvider } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import '../../src/styles.css';
import { AlgorithmNode } from '../../src/canvas/AlgorithmNode';
const query = new URLSearchParams(location.search);
document.documentElement.dataset.theme = query.get('theme') || 'light';
const preview = { viewer: 'video-grid', member_glob: '*.mp4' };
const pack = { name: 'single-video-source', version: '0.1.0', node_ids: [], manifest_hash: 'fixture',
  inputs: {}, outputs: { videos_dir: { tags: ['video-source'], storage: 'dir', preview } }, params: {} };
const data = { pack, assigned_node_id: null, previewOpen: 'videos_dir', previews: { videos_dir: {
  port_name: 'videos_dir', handle_id: 'video-background', node_id: 'fixture',
  proxy_url: '/proxy/fixture/video-background', storage: 'dir', absolute_path: '', deleted: false,
  tags: ['video-source'], preview,
} } };
createRoot(document.getElementById('root')!).render(<ReactFlowProvider>
  <ReactFlow nodes={[{ id: 'video', type: 'algorithm', position: { x: 25, y: 30 }, data }]}
    nodeTypes={{ algorithm: AlgorithmNode }} defaultViewport={{ x: 0, y: 0, zoom: 1 }} />
</ReactFlowProvider>);
