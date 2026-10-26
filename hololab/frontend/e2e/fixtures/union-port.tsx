import React from 'react';
import {createRoot} from 'react-dom/client';
import {ReactFlow,ReactFlowProvider} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import '../../src/styles.css';
import {AlgorithmNode} from '../../src/canvas/AlgorithmNode';
import {TooltipLayer} from '../../src/ui/TooltipLayer';

const union={tags:[],arrayed:false,dim_labels:[],required:true,any_of:[
  {tags:['colmap4d'],arrayed:false,dim_labels:[]},
  {tags:['colmap-folder'],arrayed:true,dim_labels:['frame']},
]};
const pack={name:'stg-init',version:'0.3.1',node_ids:[],manifest_hash:'union',inputs:{colmap_4d:union},outputs:{model:{tags:['colmap4d'],arrayed:false,dim_labels:[]},initial_state:{tags:['stg-training-state'],arrayed:false,dim_labels:[]}},params:{},arrayable:false};
const nodes=[
  {id:'source',type:'algorithm',position:{x:40,y:180},data:{pack,arrayed_toggle:false,assigned_node_id:null}},
  {id:'stg',type:'algorithm',position:{x:500,y:180},data:{pack,arrayed_toggle:false,assigned_node_id:null}},
];
const edges=[{id:'union-edge',source:'source',sourceHandle:'model',target:'stg',targetHandle:'colmap_4d'}];
createRoot(document.getElementById('root')!).render(
  <ReactFlowProvider><ReactFlow nodes={nodes} edges={edges} nodeTypes={{algorithm:AlgorithmNode}}/><TooltipLayer/></ReactFlowProvider>
);
