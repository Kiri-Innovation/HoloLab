import React, {useEffect,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {ReactFlow,ReactFlowProvider,applyNodeChanges} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import '../../src/styles.css';
import {AlgorithmNode,RUN_NODE_EVENT} from '../../src/canvas/AlgorithmNode';
import {TooltipLayer} from '../../src/ui/TooltipLayer';
import {ToastViewport} from '../../src/ui/Toast';
const pack={name:'merge-colmap',version:'0.4.0',node_ids:[],manifest_hash:'x',inputs:{},outputs:{out:{tags:['image']}},params:{},arrayable:false};
document.documentElement.dataset.theme=new URLSearchParams(location.search).get('theme') || 'light';
const types={algorithm:AlgorithmNode};
function Fixture(){
  const [nodes,setNodes]=useState(['running','done','pending','orphaned','assigned','interrupted','snapshot'].map((state,i)=>({id:state,type:'algorithm',position:{x:120+(i%3)*360,y:170+Math.floor(i/3)*240},data:{pack,assigned_node_id:null,readOnly:state==='snapshot',runtime:{state:state==='snapshot'?'running':state,job_id:state+'-parent',progress:{current:21,total:159},started_ts:Date.now()/1000-21}}})));
  useEffect(()=>{
    const update=(e:any)=>setNodes(prev=>prev.map(n=>n.id===e.detail.id?{...n,data:{...n.data,runtime:{...n.data.runtime,...e.detail.runtime}}}:n));
    const run=(e:any)=>{e.detail.handled=true;update({detail:{id:e.detail.graph_node_id,runtime:{state:'running',job_id:'new-parent'}}});e.detail.resolve();};
    window.addEventListener('fixture:runtime',update);window.addEventListener(RUN_NODE_EVENT,run);
    return()=>{window.removeEventListener('fixture:runtime',update);window.removeEventListener(RUN_NODE_EVENT,run);};
  },[]);
  return <ReactFlowProvider><ReactFlow nodes={nodes} onNodesChange={changes=>setNodes(ns=>applyNodeChanges(changes,ns))} edges={[]} nodeTypes={types}/><TooltipLayer/><ToastViewport/></ReactFlowProvider>;
}
createRoot(document.getElementById('root')!).render(<Fixture/>);
