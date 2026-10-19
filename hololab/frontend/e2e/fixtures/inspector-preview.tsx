import React, {useState} from 'react';
import {createRoot} from 'react-dom/client';
import {ReactFlow, ReactFlowProvider} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import '../../src/styles.css';
import '../../src/canvas/inspector.css';
import {AlgorithmNode, type AlgorithmNodeData} from '../../src/canvas/AlgorithmNode';
import {InspectorPreview} from '../../src/canvas/InspectorPreview';
import {SnapshotNodeInspector} from '../../src/canvas/SnapshotNodeInspector';
import {NodeInspector} from '../../src/canvas/NodeInspector';
import {CanvasContext} from '../../src/canvas/CanvasContext';
import {SnapshotCanvas} from '../../src/canvas/SnapshotCanvas';
import type {SnapshotDetail} from '../../src/wire';
const query=new URLSearchParams(location.search);
const mode=query.get('mode') || 'default';
document.documentElement.dataset.theme=query.get('theme') || 'light';
const custom=['custom','hidden','legacy'].includes(mode);
const pack:any={name:'preview-test',version:'1.0.0',node_ids:['compute'],manifest_hash:'fixture',description:'Preview policy regression fixture',params:{amount:{type:'int',default:1}},inputs:{},outputs:{result:{tags:mode==='unsupported'?['opaque']:['image'],arrayed:false,dim_labels:[],preview:null}},...(custom?{node_viewers:[{id:'custom',entry:'viewer.html',default:true}]}:{}),...(mode==='legacy'?{}:{hide_default_output_previews:mode==='hidden'})};
const target:any={handle_id:'image',port_name:'result',proxy_url:'/fixture-image.svg',node_id:'compute',storage:'file',absolute_path:'/example/output.svg',deleted:false,tags:mode==='unsupported'?['opaque']:['image'],arrayed:false,dim_labels:[],dim_sizes:[],preview:null};
const data:AlgorithmNodeData={pack,assigned_node_id:'compute',previews:mode==='empty'?{}:{result:target},previewOpen:'result'};
const graphNode:any={id:'n',algorithm_name:pack.name,algorithm_version:pack.version,position:{x:50,y:50},params:{amount:1},assigned_node_id:'compute',preview_open:'result'};
const snapshot:any={snapshot_id:'historical',workflow_id:'fixture',created_ts:1,graph:{nodes:[graphNode],edges:[]},jobs:[{input_handles:{},job_id:'historical-job',graph_node_id:'n',state:'done',algorithm_name:pack.name,algorithm_version:pack.version,params:{},output_handles:{result:'snapshot-image'}}]};
const nodeTypes={algorithm:AlgorithmNode};
function Fixture(){
 const [selected,setSelected]=useState<string|null>('n');
 const [preview,setPreview]=useState<{snapshotId:string;nodeId:string;data:AlgorithmNodeData}|null>(null);
 return <CanvasContext.Provider value={{workflow_id:'fixture',workflow_name:'Fixture',computeNodesById:{},hydrating:false}}>
 <div style={{display:'grid',gridTemplateColumns:'minmax(0,1fr) minmax(0,350px)',height:'100vh'}}>
 <ReactFlowProvider>{mode==='snapshot'?<SnapshotCanvas snapshot={snapshot as SnapshotDetail} catalog={[pack]} selectedGraphNodeId={selected} onSelectionChange={setSelected} selectedEdgeId={null} onEdgeSelectionChange={()=>{}} onPreviewData={setPreview}/>:<ReactFlow nodes={[{id:'n',type:'algorithm',position:{x:50,y:50},data}]} nodeTypes={nodeTypes}/>}</ReactFlowProvider>
 <aside className="hl-inspector-sidebar"><div className="hl-sidebar-content">{mode==='snapshot'
 ? <SnapshotNodeInspector graphNodeId={selected} pack={pack} job={snapshot.jobs[0]} preview={preview&&<InspectorPreview data={preview.data}/>}/>
 : <NodeInspector selected={graphNode} pack={pack} onChange={()=>{}} onParamsChange={()=>{}} preview={<InspectorPreview data={data}/>}/>}</div></aside>
 </div></CanvasContext.Provider>;
}
createRoot(document.getElementById('root')!).render(<Fixture/>);
