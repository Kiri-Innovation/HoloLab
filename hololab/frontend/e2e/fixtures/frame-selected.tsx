import React,{useState} from 'react';
import {createRoot} from 'react-dom/client';
import {ReactFlow} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {FrameSelectedShortcut} from '../../src/canvas/FrameSelectedShortcut';
function Fixture(){
 const [size,setSize]=useState(20);
 return <><button onClick={()=>setSize(20)}>Small</button><button onClick={()=>setSize(5000)}>Large</button>
 <div style={{width:600,height:400}}><ReactFlow nodes={[{id:'selected',position:{x:0,y:0},data:{label:'Selected'},style:{width:size,height:size}}]} minZoom={0.3} maxZoom={1.4}>
 <FrameSelectedShortcut nodeId="selected" /></ReactFlow></div></>;
}
createRoot(document.getElementById('root')!).render(<Fixture/>);
