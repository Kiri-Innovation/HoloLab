import React from 'react';
import {createRoot} from 'react-dom/client';
import {ReactFlowProvider} from '@xyflow/react';
import '../../src/styles.css';
import {Preview} from '../../src/canvas/previews';
const query = new URLSearchParams(location.search);
document.documentElement.dataset.theme = query.get('theme') || 'light';
createRoot(document.getElementById('root')!).render(<ReactFlowProvider>
  <main style={{width:480, padding:20}}>
    <h3>Nested image array</h3>
    <Preview baseUrl="/proxy/alpha/images" storage="dir" tags={['rig_frames']} handleId="alpha-nested" />
    <h3>Timeline image</h3>
    <Preview baseUrl="/proxy/alpha/timeline" storage="dir" tags={['rig_timeline']} />
  </main>
</ReactFlowProvider>);
