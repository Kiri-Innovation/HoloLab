import React, {useState} from 'react';
import {createRoot} from 'react-dom/client';
import {ReactFlow, ReactFlowProvider} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import '../../src/styles.css';
import {CanvasContext} from '../../src/canvas/CanvasContext';
import {ImageCropEditor} from '../../src/canvas/ImageCropEditor';
const query=new URLSearchParams(location.search);
function Fixture(){
 const [params,setParams]=useState({pad_left:-10,pad_top:5,pad_right:20,pad_bottom:10});
 const [writes,setWrites]=useState(0);
 const nodes=[{id:'crop',position:{x:0,y:0},data:{params,pack:{params:{},inputs:{}}}},{id:'source',position:{x:0,y:0},data:{previews:{image:{handle_id:'fixture-image'}}}}];
 return <CanvasContext.Provider value={{onParamsChange:(_id,patch)=>{setWrites(n=>n+1);setParams(p=>({...p,...patch}));}} as any}>
  <ReactFlow nodes={nodes} edges={query.has('missing')?[]:[{id:'edge',source:'source',target:'crop',sourceHandle:'image',targetHandle:'image'}]} />
  <div style={{position:'absolute',top:20,left:20,width:500}}><ImageCropEditor id="crop" readOnly={query.has('readonly')} /><output>{writes}</output></div>
 </CanvasContext.Provider>;
}
createRoot(document.getElementById('root')!).render(<ReactFlowProvider><Fixture/></ReactFlowProvider>);
