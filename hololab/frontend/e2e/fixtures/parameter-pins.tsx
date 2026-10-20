import React, {useState} from 'react';
import {useDraftAutosave} from '../../src/canvas/useDraftAutosave';
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
if (new URLSearchParams(location.search).has("preview-width")) pack.outputs = {out:{tags:["unknown-test-artifact"],storage:"dir"}};
function AutosaveFixture() {
  const [value,setValue]=useState('original');
  const [saved,setSaved]=useState<string[]>([]);
  const [reloads,setReloads]=useState(0);
  const autosave=useDraftAutosave({serialisedSnapshot:JSON.stringify({value}),enabled:true,initialBaseUpdatedTs:1,
    save:async()=>{setSaved(old=>[...old,value]);return {workflow_id:'fixture',updated_ts:2};},beaconBody:()=>null});
  return <div><input aria-label="Value" value={value} onChange={e=>setValue(e.target.value)}/>
    <button onClick={()=>{autosave.resync(1);setReloads(n=>n+1);}}>Reload unchanged workflow</button>
    <output aria-label="Reloads">{reloads}</output><output aria-label="Saves">{JSON.stringify(saved)}</output></div>;
}
createRoot(document.getElementById('root')!).render(new URLSearchParams(location.search).has('autosave') ? <AutosaveFixture/> : (
  <CanvasContext.Provider value={{workflow_id:'fixture',workflow_name:'Frozen parameter pins — test fixture',computeNodesById:{},hydrating:false,pinnedParams:{'pin-demo@1.0':['label']},onParamsChange:()=>{throw Error('Snapshot attempted an edit');}}}>
    <div style={{height:'100vh'}}><ReactFlowProvider><SnapshotCanvas snapshot={snapshot} catalog={[pack]} selectedGraphNodeId={null} onSelectionChange={()=>{}} selectedEdgeId={null} onEdgeSelectionChange={()=>{}} /></ReactFlowProvider></div>
  </CanvasContext.Provider>
));
