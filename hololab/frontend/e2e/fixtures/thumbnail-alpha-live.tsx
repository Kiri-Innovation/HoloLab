import React from 'react';
import {createRoot} from 'react-dom/client';
import {ReactFlowProvider} from '@xyflow/react';
import '../../src/styles.css';
import {Preview} from '../../src/canvas/previews';
const query = new URLSearchParams(location.search);
document.documentElement.dataset.theme = query.get('theme') || 'light';
const base = query.get('base')!;
createRoot(document.getElementById('root')!).render(<ReactFlowProvider>
  <main style={{padding:24,width:420}}>
    <section><h3>Array tile — live JPEG /_thumb/</h3>
      <Preview baseUrl={base} storage="dir" handleId="alpha-live-fixture" tags={['image']} arrayed dimLabels={['frame']} />
      <p>Real mask-to-alpha frame recovered from the previous trace.</p>
    </section>
  </main>
</ReactFlowProvider>);
