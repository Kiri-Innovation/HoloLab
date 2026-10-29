import React from 'react';
import { createRoot } from 'react-dom/client';
import { ReactFlow, ReactFlowProvider } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import '../../src/styles.css';
import { AlgorithmNode } from '../../src/canvas/AlgorithmNode';
import { CanvasContext } from '../../src/canvas/CanvasContext';

document.documentElement.dataset.theme = new URLSearchParams(location.search).get('theme') || 'light';
const port = { tags:['float'], storage:'file', required:true };
const pack = { name:'inline-demo', version:'1', node_ids:[], manifest_hash:'inline-demo', inputs:{amount:port}, outputs:{}, params:{} };
const nodes = [
  { id:'collapsed', type:'algorithm', position:{x:140,y:150}, data:{pack, assigned_node_id:null} },
  { id:'expanded', type:'algorithm', position:{x:500,y:150}, data:{pack, assigned_node_id:null, port_values:{amount:2}} },
];
const context = { workflow_id:null, workflow_name:'test', computeNodesById:{}, hydrating:false,
  viewer_view:null, viewer_view_loaded:false, viewer_view_managed:false, onInlineValueChange:()=>{} };
createRoot(document.getElementById('root')!).render(
  <CanvasContext.Provider value={context}>
    <ReactFlowProvider><ReactFlow nodes={nodes} nodeTypes={{algorithm:AlgorithmNode}} fitView /></ReactFlowProvider>
  </CanvasContext.Provider>,
);
