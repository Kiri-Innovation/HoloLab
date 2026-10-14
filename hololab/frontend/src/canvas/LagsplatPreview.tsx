import { useEffect, useRef } from "react";

// Type-level renderer for the lagsplat_model contract.  The state directory
// contains viewer_data/index.json -> gaussians.js + dynamics.js, exported by
// the producer.  The iframe inherits this application's origin, so it fetches
// only the gateway proxy URL supplied below; it never reads torch checkpoints.
const HTML = String.raw`<!doctype html><style>body{margin:0;background:#11151c;color:#e9edf5;font:12px system-ui;padding:8px}canvas{display:block;width:min(100%,420px);margin:8px auto;background:#000;border-radius:5px}button,input{accent-color:#7cb6ff}#s{color:#a9c8ee}</style><b>LaGSplat Viewer</b><span id=s>Loading model…</span><canvas id=c width=128 height=128></canvas><button id=p>Pause</button> <button id=r>Reset</button> Time <input id=t type=range min=0 max=1000 value=0><script>(()=>{let D,S,on=1,last=0,e=0;const c=document.querySelector('#c'),x=c.getContext('2d'),s=document.querySelector('#s');addEventListener('message',async q=>{if(q.data?.type!=='lagsplat-model')return;try{let b=q.data.url.replace(/\/[^/]*$/,'/');eval(await(await fetch(q.data.url)).text());eval(await(await fetch(b+'dynamics.js')).text());D=PEND_DATA;S=createPendSim(PEND_META);c.width=c.height=D.tile||128;s.textContent=D.n_gaussians+' Gaussians · OURS: LNN ROLLOUT';draw()}catch(z){s.textContent='Viewer data unavailable: '+z.message}});function draw(){if(!D)return;let W=c.width,H=c.height,i=x.createImageData(W,H),z=S.toZ(S.state.z);for(let g of D.gaussians){let dz=z.map((v,n)=>v-(Array.isArray(g.mz)?g.mz[n]:g.mz)),q=dz.reduce((a,v,n)=>a+v*v*(Array.isArray(g.szzi)?g.szzi[n]:g.szzi),0),o=Math.min(.99,g.a*Math.exp(-q/2));if(o<.004)continue;let X=g.p[0]+(g.jac[0]||0)*dz[0],Y=g.p[1]+(g.jac[1]||0)*dz[0],A=g.scinv[0],B=g.scinv[1],C=g.scinv[2],d=A*C-B*B;if(!(d>0))continue;let R=Math.ceil(3*Math.sqrt(Math.max(C/d,A/d))*W);for(let yy=Math.max(0,Y*H-R|0);yy<=Math.min(H-1,Y*H+R|0);yy++)for(let xx=Math.max(0,X*W-R|0);xx<=Math.min(W-1,X*W+R|0);xx++){let u=(xx+.5)/W-X,v=(yy+.5)/H-Y,h=A*u*u+2*B*u*v+C*v*v;if(h>18)continue;let a=o*Math.exp(-h/2),k=4*(yy*W+xx),m=1-a;i.data[k]=g.c[0]*255*a+i.data[k]*m;i.data[k+1]=g.c[1]*255*a+i.data[k+1]*m;i.data[k+2]=g.c[2]*255*a+i.data[k+2]*m;i.data[k+3]=255}}x.putImageData(i,0,0);s.textContent='OURS: LNN ROLLOUT · z '+z[0].toFixed(3)}function loop(n){if(last&&S&&on){let d=Math.min(.05,(n-last)/1000);S.tick(d);e+=d;t.value=Math.min(1000,e*100);draw()}last=n;requestAnimationFrame(loop)}requestAnimationFrame(loop);p.onclick=()=>{on=!on;p.textContent=on?'Pause':'Play'};r.onclick=()=>{S.reset();e=0;draw()};t.oninput=()=>{S.reset();for(let n=0;n<t.value/100*30;n++)S.tick(1/30);e=t.value/100;draw()}})()</script>`;

export function LagsplatPreview({ baseUrl }: { baseUrl: string }) {
  const ref = useRef<HTMLIFrameElement>(null);
  useEffect(() => {
    const frame = ref.current;
    if (!frame) return;
    const send = () => frame.contentWindow?.postMessage(
      { type: "lagsplat-model", url: `${baseUrl.replace(/\/$/, "")}/viewer_data/gaussians.js` },
      window.location.origin,
    );
    frame.addEventListener("load", send);
    return () => frame.removeEventListener("load", send);
  }, [baseUrl]);
  return <iframe ref={ref} title="LaGSplat Viewer" srcDoc={HTML} style={{ width: "100%", height: 270, border: 0 }} />;
}
