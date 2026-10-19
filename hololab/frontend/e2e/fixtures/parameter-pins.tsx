import React from 'react';
import {createRoot} from 'react-dom/client';
import {ReactFlowProvider} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import '../../src/styles.css';
import '../../src/canvas/inspector.css';
import {CanvasContext} from '../../src/canvas/CanvasContext';
import {SnapshotCanvas} from '../../src/canvas/SnapshotCanvas';
const pack:any={name:'pin-demo',version:'1.0',node_ids:[],manifest_hash:'fixture',params:{count:{type:'int',default:1},label:{type:'string',default:'current default'}},inputs:{},outputs:{}};
const snapshot:any={snapshot_id:'frozen',workflow_id:'fixture',created_ts:1,
  graph:{pinned_params:{'pin-demo@1.0':['count','removed']},nodes:[{id:'n',algorithm_name:pack.name,algorithm_version:pack.version,position:{x:80,y:80},params:{count:42,label:'historical'},assigned_node_id:null}],edges:[]},jobs:[]};
createRoot(document.getElementById('root')!).render(
  <CanvasContext.Provider value={{workflow_id:'fixture',workflow_name:'Frozen parameter pins — test fixture',computeNodesById:{},hydrating:false,pinnedParams:{'pin-demo@1.0':['label']},onParamsChange:()=>{throw Error('Snapshot attempted an edit');}}}>
    <div style={{height:'100vh'}}><ReactFlowProvider><SnapshotCanvas snapshot={snapshot} catalog={[pack]} selectedGraphNodeId={null} onSelectionChange={()=>{}} selectedEdgeId={null} onEdgeSelectionChange={()=>{}} /></ReactFlowProvider></div>
  </CanvasContext.Provider>
);
