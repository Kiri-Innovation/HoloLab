import React from 'react';
import { createRoot } from 'react-dom/client';
import { ReactFlow, ReactFlowProvider } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import '../../src/styles.css';
import { AlgorithmNode } from '../../src/canvas/AlgorithmNode';
import { TooltipLayer } from '../../src/ui/TooltipLayer';

const pack={node_ids:[],manifest_hash:'fixture',description:'',category:[],name:'merge-colmap',version:'0.4.0',inputs:{cams:{tags:['camera']}},outputs:{out:{tags:['image']}},params:{},arrayable:false};
const cases=[
  ['idle',undefined,'self_dirty'],['pending','pending'],['done','done'],['stale','done','upstream_dirty'],
  ['failed','failed'],['orphaned','orphaned'],['interrupted','interrupted'],['drift','running','inflight_old_params'],
  ['cancelled','cancelled'],['unknown','future'],['snapshot','done'],
];
const query=new URLSearchParams(location.search);
document.documentElement.dataset.theme=query.get('theme') || 'light';
const nodes=cases.map(([id,state,kind],i)=>({id:id!,type:'algorithm',position:{x:100+(i%4)*350,y:190+Math.floor(i/4)*250},data:{pack,assigned_node_id:null,
  runtime:state?{state,fail_reason:state==='failed'?'磁盘空间不足':undefined}:undefined,
  readOnly:id==='snapshot',staleness:kind?{kind,title:kind==='inflight_old_params'?'参数已改：max_width':kind==='upstream_dirty'?'上游输入已变化，本节点结果基于旧输入':'尚未运行'}:null}}));
createRoot(document.getElementById('root')!).render(<ReactFlowProvider><ReactFlow nodes={nodes} edges={[{id:'link',source:'idle',target:'pending',sourceHandle:'out',targetHandle:'cams'}]} nodeTypes={{algorithm:AlgorithmNode}} defaultViewport={{x:0,y:0,zoom:1}}/><TooltipLayer/></ReactFlowProvider>);
