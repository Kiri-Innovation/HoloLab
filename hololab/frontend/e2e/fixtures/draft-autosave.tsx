import React,{useState} from 'react';
import {createRoot} from 'react-dom/client';
import {useDraftAutosave} from '../../src/canvas/useDraftAutosave';
import {SaveStatusPill} from '../../src/canvas/SaveStatusPill';
import {ApiError} from '../../src/api';
function Fixture(){
  const [value,setValue]=useState('initial');
  const [enabled,setEnabled]=useState(true);
  const [run,setRun]=useState('none');
  const a=useDraftAutosave({enabled,serialisedSnapshot:value,initialBaseUpdatedTs:1,
    save:async(base,signal)=>{const r=await fetch('/test-save',{signal,method:'POST',body:JSON.stringify({value,base})});const b=await r.json();if(!r.ok)throw new ApiError(r.status,b.detail);return b;},
    beaconBody:base=>({url:'/test-save',body:JSON.stringify({value,base})})});
  return <><SaveStatusPill status={a.status} onRetry={()=>void a.save().catch(()=>{})}/><input aria-label="Value" value={value} onChange={e=>setValue(e.target.value)}/>
    <button onClick={()=>void a.save().then(()=>setRun('ready'),()=>setRun('blocked'))}>Save before Run</button>
    <button onClick={()=>setEnabled(!enabled)}>Toggle read only</button>
    <output aria-label="Save status">{a.status}</output><output aria-label="Run gate">{run}</output></>;
}
createRoot(document.getElementById('root')!).render(<Fixture/>);
