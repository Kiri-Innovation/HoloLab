import React from 'react';
import {createRoot} from 'react-dom/client';
import {ReactFlow,ReactFlowProvider} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import '../../src/styles.css';
import {AlgorithmNode} from '../../src/canvas/AlgorithmNode';
import {TypedEdge} from '../../src/canvas/TypedEdge';
import {TooltipLayer} from '../../src/ui/TooltipLayer';
import {seedEdgeSummaryFacts} from '../../src/canvas/edgeSummaryCache';

const complete=(value:unknown,source='directory-tree-scan')=>({evidence:{status:'complete',value,source}});
const runtime=(handleId:string,tags:string[],axes:unknown[])=>({schemaVersion:1,handleId,
 tags:complete(tags,'registered-handle-metadata'),axes:complete(axes,'handle-shape-probe'),internalCounts:[]});
const exact=(label:string|null,value:number)=>({label,role:'collection',size:complete(value)});
seedEdgeSummaryFacts('images',{handle_id:'images',tags:['image'],dim_labels:['group','cam'],deleted:false,
 runtime_shape:runtime('images',['image'],[exact('group',159),{label:'cam',role:'collection',
  size:{evidence:{status:'unknown',reason:'ragged'}},sizeRange:complete({min:6,max:7})}])});
seedEdgeSummaryFacts('cams',{handle_id:'cams',tags:['colmap-cams'],dim_labels:['group'],deleted:false,
 runtime_shape:runtime('cams',['colmap-cams'],[exact('group',159)])});
seedEdgeSummaryFacts('flat',{handle_id:'flat',tags:['image'],dim_labels:[''],deleted:false,
 runtime_shape:runtime('flat',['image'],[exact(null,1110)])});

const common={version:'test',node_ids:[],manifest_hash:'test',params:{}};
const und={...common,name:'image-undistort',arrayable:true,inputs:{},outputs:{
 cams:{tags:['colmap-cams'],arrayed:false,dim_labels:[]},images:{tags:['image'],arrayed:true,dim_labels:['cam']}}};
const flatten={...common,name:'flatten-array',inputs:{arr:{tags:['any'],arrayed:true,dim_labels:['outer','inner'],required:true}},outputs:{
 flat:{tags:['any'],arrayed:true,dim_labels:[''],tags_from:'arr'}}};
const merge={...common,name:'merge-colmap4d',inputs:{
 points:{tags:['point-cloud'],arrayed:true,dim_labels:['group'],required:true},
 images:{tags:['image'],arrayed:true,dim_labels:[''],required:true},
 cams:{tags:['colmap-cams'],arrayed:true,dim_labels:['group'],required:true}},outputs:{model:{tags:['colmap4d'],arrayed:false,dim_labels:[]}}};
const nodes=[
 {id:'und_g0',type:'algorithm',position:{x:80,y:250},data:{pack:und,arrayed_toggle:true,assigned_node_id:null}},
 {id:'flatten_g0',type:'algorithm',position:{x:540,y:170},data:{pack:flatten,arrayed_toggle:false,assigned_node_id:null}},
 {id:'merge4d_g0',type:'algorithm',position:{x:1020,y:250},data:{pack:merge,arrayed_toggle:false,assigned_node_id:null}},
];
const edges=[
 {id:'images',source:'und_g0',sourceHandle:'images',target:'flatten_g0',targetHandle:'arr',type:'typed',data:{edgeType:{tags:['image'],arrayed:true,dimLabels:['group','cam']},handleId:'images'}},
 {id:'cams',source:'und_g0',sourceHandle:'cams',target:'merge4d_g0',targetHandle:'cams',type:'typed',data:{edgeType:{tags:['colmap-cams'],arrayed:true,dimLabels:['group']},handleId:'cams'}},
 {id:'flat',source:'flatten_g0',sourceHandle:'flat',target:'merge4d_g0',targetHandle:'images',type:'typed',data:{edgeType:{tags:['image'],arrayed:true,dimLabels:['']},handleId:'flat'}},
];
createRoot(document.getElementById('root')!).render(<ReactFlowProvider><ReactFlow nodes={nodes} edges={edges}
 nodeTypes={{algorithm:AlgorithmNode}} edgeTypes={{typed:TypedEdge}} fitView/><TooltipLayer/></ReactFlowProvider>);
