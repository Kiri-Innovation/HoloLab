import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import '../../src/styles.css';
import '../../src/canvas/inspector.css';
import { InlineInputEditor } from '../../src/canvas/InlineInputEditor';

document.documentElement.dataset.theme = new URLSearchParams(location.search).get('theme') || 'light';

const pack: any = {
  inputs: {
    explicit: { tags: ['float'], storage: 'file', required: true },
    fallback: { tags: ['float'], storage: 'file', required: true, inline_default: 2 },
    format: { tags: ['float'], storage: 'file', required: false },
    upstream: { tags: ['image'], storage: 'file', required: true, inline: false },
  },
  arrayable: false,
};

function Fixture() {
  const [node, setNode] = useState<any>({
    id: 'inline-input-fixture', algorithm_name: 'fixture', algorithm_version: '1',
    position: { x: 0, y: 0 }, params: {}, port_values: { explicit: 3 }, assigned_node_id: null,
  });
  return <main className="hl-inspector-parameters" style={{ width: 360, padding: 20 }}>
    <section className="hl-inspector-inputs hl-inspector-parameters" aria-label="Input values">
      <h3 style={{ margin: '0 0 10px', fontSize: 'var(--fs-xs)' }}>Input values</h3>
      <InlineInputEditor node={node} pack={pack} onChange={port_values => setNode({ ...node, port_values })} />
    </section>
  </main>;
}

createRoot(document.getElementById('root')!).render(<Fixture />);
