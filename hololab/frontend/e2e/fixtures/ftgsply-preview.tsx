import React from 'react';
import { createRoot } from 'react-dom/client';
import { ReactFlowProvider } from '@xyflow/react';
import { Preview } from '../../src/canvas/previews';
import type { OutputPreviewSpec } from '../../src/wire';
const query = new URLSearchParams(location.search);
const viewer = query.get('viewer') || 'ftgsply';
const spec = query.has('fallback') ? null : {viewer, member: query.get('member')};
createRoot(document.getElementById('root')!).render(<ReactFlowProvider>
  <Preview spec={spec as OutputPreviewSpec} tags={['ftgsply']} storage={query.get('storage') === 'dir' ? 'dir' : 'file'} baseUrl="/sample/model.ftgs.ply" />
</ReactFlowProvider>);
