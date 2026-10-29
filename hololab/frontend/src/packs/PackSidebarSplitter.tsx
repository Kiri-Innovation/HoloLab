import {useEffect, useRef, useState, type PointerEvent} from 'react';

const KEY = 'hololab.pack-manager.sidebar-width';
const clamp = (value:number, max=480) => Math.max(200, Math.min(max,value));
export function usePackSidebarWidth() {
  const [width,setWidth]=useState(()=>{
    try {const raw=localStorage.getItem(KEY); const n=Number(raw); return raw !== null && Number.isFinite(n) ? clamp(n) : 300;} catch {return 300;}
  });
  const commit=(value:number)=>{
    const next=clamp(value);setWidth(next);
    try {localStorage.setItem(KEY,String(next));} catch {/* Storage may be disabled. */}
  };
  return {width,commit};
}

/** Move only a compositor guide during drag; commit grid layout once on release. */
export function PackSidebarSplitter({width,onCommit}:{width:number;onCommit:(value:number)=>void}) {
  const ref=useRef<HTMLDivElement>(null);
  const drag=useRef<{start:number;base:number;next:number;max:number}|null>(null);
  const frame=useRef(0);
  const stop=()=>{
    cancelAnimationFrame(frame.current);frame.current=0;drag.current=null;
    if(ref.current){ref.current.removeAttribute('data-dragging');ref.current.style.removeProperty('--pack-resize-offset');}
  };
  useEffect(()=>()=>{cancelAnimationFrame(frame.current);},[]);
  const bounds=()=>Math.min(480,Math.max(200,(ref.current?.parentElement?.clientWidth ?? 806)-326));
  const move=(event:PointerEvent<HTMLDivElement>)=>{
    const current=drag.current;if(!current)return;
    current.next=clamp(current.base+event.clientX-current.start,current.max);
    if(!frame.current)frame.current=requestAnimationFrame(()=>{
      frame.current=0;if(drag.current)ref.current?.style.setProperty('--pack-resize-offset',`${drag.current.next-drag.current.base}px`);
    });
  };
  return <div ref={ref} className="hl-pack-splitter" role="separator" tabIndex={0}
    aria-label="调整包列表宽度" aria-orientation="vertical" aria-valuemin={200} aria-valuemax={480} aria-valuenow={width}
    title="拖动调整宽度，双击恢复默认；方向键微调"
    onPointerDown={event=>{
      if(event.button!==0 || !matchMedia('(min-width: 761px)').matches)return;
      event.preventDefault();event.currentTarget.focus();
      const base=event.currentTarget.previousElementSibling!.getBoundingClientRect().width;
      drag.current={start:event.clientX,base,next:base,max:bounds()};
      event.currentTarget.setPointerCapture(event.pointerId);event.currentTarget.setAttribute('data-dragging','true');
    }} onPointerMove={move}
    onPointerUp={event=>{
      if(!drag.current)return;move(event);const next=drag.current!.next;stop();onCommit(next);
      if(event.currentTarget.hasPointerCapture(event.pointerId))event.currentTarget.releasePointerCapture(event.pointerId);
    }} onPointerCancel={stop} onLostPointerCapture={stop}
    onDoubleClick={()=>{stop();onCommit(300);}}
    onKeyDown={event=>{
      if(!['ArrowLeft','ArrowRight','Home','End','Escape'].includes(event.key))return;
      event.preventDefault();if(event.key==='Escape'){stop();return;}
      const actual=event.currentTarget.previousElementSibling!.getBoundingClientRect().width;
      onCommit(event.key==='Home'?200:event.key==='End'?bounds():clamp(actual+(event.key==='ArrowRight'?10:-10),bounds()));
    }}><span aria-hidden="true" /></div>;
}
