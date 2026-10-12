import React, {useState} from 'react';
import {createRoot} from 'react-dom/client';
import {ReactFlowProvider,useReactFlow} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import '../../src/styles.css';
import {SnapshotCanvas} from '../../src/canvas/SnapshotCanvas';
import {TooltipLayer} from '../../src/ui/TooltipLayer';
import type {CatalogPack, SnapshotDetail} from '../../src/wire';
const port={tags:['colmap-cams'],arrayed:true,dim_labels:['frame'],required:true};
const pack={name:'merge-colmap',version:'0.4.0',node_ids:[],manifest_hash:'fixture',inputs:{cams:port},outputs:{cams:port},params:{},arrayable:true} as unknown as CatalogPack;
const legacy=new URLSearchParams(location.search).has('legacy');
const snapshot={snapshot_id:'historical',workflow_id:'fixture',created_ts:1,jobs:legacy?[{job_id:'old-job',graph_node_id:'n0',state:'done',algorithm_name:pack.name,algorithm_version:pack.version,params:{},output_handles:{cams:'old-handle'},created_ts:1,started_ts:2,finished_ts:3}]:[],graph:{nodes:[0,1].map(i=>({id:`n${i}`,algorithm_name:pack.name,algorithm_version:pack.version,position:{x:i*460,y:150},params:{},assigned_node_id:null,arrayed_toggle:true,
 resolved_outputs:{cams:{tags:['colmap-cams'],arrayed:true,dim_labels:['historical-frame']}}})),edges:[{id:'historic-edge',source:'n0',sourceHandle:'cams',target:'n1',targetHandle:'cams'}]}} as SnapshotDetail;
document.documentElement.dataset.theme=new URLSearchParams(location.search).get('theme') || 'light';
const catalog = [pack];
const mixed=new URLSearchParams(location.search).has('mixed');
function ZoomControls(){
 const {setViewport}=useReactFlow();
 return <div style={{position:'fixed',top:0,right:0,zIndex:100}}>{[0.5,1,1.5].map(zoom=><button key={zoom} onClick={()=>setViewport({x:40,y:30,zoom})}>Zoom {zoom}</button>)}</div>;
}
function Fixture(){
 const [arrayed,setArrayed]=useState(true);
 const shown=mixed?{...snapshot,snapshot_id:arrayed?'arrayed':'ordinary',graph:{...snapshot.graph,nodes:snapshot.graph.nodes.map((n,i)=>({...n,arrayed_toggle:i===0 && arrayed}))}}:snapshot;
 const [edge,setEdge]=useState<string|null>(null); const [node,setNode]=useState<string|null>(null);
 return <ReactFlowProvider>{mixed && <ZoomControls/>}{mixed && <button style={{position:"fixed",top:0,zIndex:100}} onClick={()=>setArrayed(v=>!v)}>Switch snapshot</button>}<SnapshotCanvas snapshot={shown} catalog={catalog} selectedGraphNodeId={node} onSelectionChange={setNode} selectedEdgeId={edge} onEdgeSelectionChange={setEdge}/><TooltipLayer/></ReactFlowProvider>;
}
createRoot(document.getElementById('root')!).render(<Fixture/>);
