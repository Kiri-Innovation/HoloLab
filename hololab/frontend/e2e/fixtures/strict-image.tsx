import React from 'react';
import {createRoot} from 'react-dom/client';
import {ReactFlow,ReactFlowProvider} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import '../../src/styles.css';
import {AlgorithmNode} from '../../src/canvas/AlgorithmNode';
import {TypedEdge} from '../../src/canvas/TypedEdge';
import {TooltipLayer} from '../../src/ui/TooltipLayer';
import {Preview} from '../../src/canvas/previews';
import {seedEdgeSummaryFacts} from '../../src/canvas/edgeSummaryCache';
import {measuredPayload} from '../../src/test/measurements';
const q=new URLSearchParams(location.search);
document.documentElement.dataset.theme=q.get('theme')||'light';
const input={tags:['image'],arrayed:true,storage:'dir',dim_labels:['cam'],required:true};
const pack={name:'community/image-undistort',version:'0.5.2',node_ids:[],manifest_hash:'fixture',arrayable:true,
 inputs:{images:input,cams:{tags:['colmap-cams'],scalar:true,required:true}},outputs:{images:input},params:{}};
seedEdgeSummaryFacts('images',{handle_id:'images',tags:['image'],dim_labels:['frame','cam'],deleted:false,runtime_shape:measuredPayload(['image'],['frame','cam'],[100,21],[],'images')});
seedEdgeSummaryFacts('cams',{handle_id:'cams',tags:['colmap-cams'],dim_labels:[],deleted:false,runtime_shape:measuredPayload(['colmap-cams'],[],[],[{key:'pose',value:21},{key:'intr',value:1}],'cams')});
const source={...pack,name:'source',arrayable:false,inputs:{},outputs:{images:{...input,dim_labels:['frame','cam']},cams:{tags:['colmap-cams']}}};
const nodes=[{id:'s',type:'algorithm',position:{x:40,y:230},data:{pack:source,assigned_node_id:null}},
{id:'und',type:'algorithm',position:{x:520,y:230},data:{pack,arrayed_toggle:true,assigned_node_id:null,previews:{images:{handle_id:'images',tags:['image']}}}}];
const edges=['images','cams'].map(name=>({id:name,source:'s',sourceHandle:name,target:'und',targetHandle:name,type:'typed',data:{handleId:name,edgeType:{tags:[name==='images'?'image':'colmap-cams'],arrayed:name==='images',dimLabels:name==='images'?['frame','cam']:[]}}}));
createRoot(document.getElementById('root')!).render(<><div style={{width:1200,height:650}}><ReactFlowProvider><ReactFlow nodes={nodes} edges={edges} nodeTypes={{algorithm:AlgorithmNode}} edgeTypes={{typed:TypedEdge}}/><TooltipLayer/></ReactFlowProvider></div><div id="single" style={{width:220,margin:20}}><strong>image = one file</strong><Preview tags={['image']} storage="file" baseUrl="/fixture-image.png"/></div></>);
