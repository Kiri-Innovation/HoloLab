import React, {useEffect,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {ReactFlow,ReactFlowProvider,applyNodeChanges} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import '../../src/styles.css';
import {AlgorithmNode,RUN_NODE_EVENT} from '../../src/canvas/AlgorithmNode';
import {CanvasContext} from '../../src/canvas/CanvasContext';
import {TooltipLayer} from '../../src/ui/TooltipLayer';
import {ToastViewport} from '../../src/ui/Toast';
const pack={name:'merge-colmap',version:'0.4.0',node_ids:[],manifest_hash:'x',inputs:{},outputs:{out:{tags:['image']}},params:{},arrayable:false};
document.documentElement.dataset.theme=new URLSearchParams(location.search).get('theme') || 'light';
const types={algorithm:AlgorithmNode};
function Fixture(){
  const [nodes,setNodes]=useState(['running','done','pending','orphaned','assigned','interrupted','snapshot','dispatching','failed-dispatch','blocked-running','blocked-never','blocked-output'].map((state,i)=>({id:state,type:'algorithm',position:{x:120+(i%3)*360,y:170+Math.floor(i/3)*240},data:{pack,assigned_node_id:null,readOnly:state==='snapshot',runtime:{state:['dispatching','failed-dispatch','blocked-running','blocked-never','blocked-output'].includes(state)?'done':state==='snapshot'?'running':state,job_id:state+'-parent',progress:{current:21,total:159},started_ts:Date.now()/1000-21}}})));
  useEffect(()=>{
    const update=(e:any)=>setNodes(prev=>prev.map(n=>n.id===e.detail.id?{...n,data:{...n.data,runtime:{...n.data.runtime,...e.detail.runtime}}}:n));
    const run=(e:any)=>{e.detail.handled=true;if(e.detail.graph_node_id==='dispatching')return;if(e.detail.graph_node_id==='failed-dispatch'){e.detail.reject('模拟派发失败');return;}update({detail:{id:e.detail.graph_node_id,runtime:{state:'running',job_id:'new-parent'}}});e.detail.resolve();};
    window.addEventListener('fixture:runtime',update);window.addEventListener(RUN_NODE_EVENT,run);
    return()=>{window.removeEventListener('fixture:runtime',update);window.removeEventListener(RUN_NODE_EVENT,run);};
  },[]);
  return <CanvasContext.Provider value={{workflow_id:'fixture',workflow_name:'fixture',computeNodesById:{},hydrating:false,upstreamBlockerByNode:{'blocked-running':'上游节点 upstream 正在运行（2/252 分片已完成）；请等待完成后重试。','blocked-never':'上游节点 upstream 尚未运行；请先运行上游。','blocked-output':'上游节点 upstream 未生成端口 mask 的产物；请重新运行上游。'},viewer_view:null,viewer_view_loaded:true,viewer_view_managed:false}}><ReactFlowProvider><ReactFlow nodes={nodes} onNodesChange={changes=>setNodes(ns=>applyNodeChanges(changes,ns))} edges={[]} nodeTypes={types}/><TooltipLayer/><ToastViewport/></ReactFlowProvider></CanvasContext.Provider>;
}
createRoot(document.getElementById('root')!).render(<Fixture/>);
