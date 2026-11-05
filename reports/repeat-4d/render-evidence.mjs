// Isolated real SnapshotCanvas rendering; no API writes or frontend file changes.
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import fs from 'node:fs';
const here=path.dirname(fileURLToPath(import.meta.url));
const root=path.resolve(here,'../../hololab/frontend');
const require=createRequire(path.join(root,'package.json'));
const {createServer}=await import(path.join(path.dirname(require.resolve('vite/package.json')),'dist/node/index.js'));
const {chromium}=require('@playwright/test');
const pack=JSON.parse(fs.readFileSync(path.join(here,'catalog.json')));
const virtual=path.join(root,'repeat-evidence.jsx');
const code=`import React from 'react';
import {createRoot} from 'react-dom/client';
import {ReactFlowProvider} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import './src/styles.css';
import {SnapshotCanvas} from './src/canvas/SnapshotCanvas';
import {TooltipLayer} from './src/ui/TooltipLayer';
const pack=${JSON.stringify({...pack,node_ids:[],manifest_hash:'local-evidence'})};
const snapshot={snapshot_id:'repeat-port-evidence',workflow_id:'fixture',created_ts:1,jobs:[],graph:{nodes:[{id:'repeat',algorithm_name:pack.name,algorithm_version:pack.version,position:{x:100,y:100},params:{duration:1,fps:30,start_time_ns:0},assigned_node_id:null,arrayed_toggle:false}],edges:[]}};
document.documentElement.dataset.theme=new URLSearchParams(location.search).get('theme')||'light';
createRoot(document.getElementById('root')).render(<ReactFlowProvider><SnapshotCanvas snapshot={snapshot} catalog={[pack]} selectedGraphNodeId={null} onSelectionChange={()=>{}} selectedEdgeId={null} onEdgeSelectionChange={()=>{}}/><TooltipLayer/></ReactFlowProvider>);`;
const server=await createServer({configFile:false,root,define:{__EMBED_BUILD_ID__:JSON.stringify('repeat-evidence')},server:{host:'127.0.0.1',port:5198,strictPort:true},plugins:[{name:'repeat-evidence',resolveId(id){if(id==='/repeat-evidence.jsx')return virtual;},load(id){if(id===virtual)return code;},configureServer(s){s.middlewares.use((req,res,next)=>{if(req.url.split('?')[0]!=='/repeat-evidence')return next();res.setHeader('Content-Type','text/html');res.end('<html><body style="margin:0"><div id="root" style="height:800px"></div><script type="module" src="/repeat-evidence.jsx"></script></body></html>');});}}]});
let browser;
try{
 await server.listen();browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 const page=await browser.newPage({viewport:{width:1200,height:800}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/**',r=>r.fulfill({json:[]}));
 for(const theme of ['light','dark']){
  await page.goto(`http://127.0.0.1:5198/repeat-evidence?theme=${theme}`);
  await page.locator('[data-id="repeat"] .target[data-handleid="camera"]').waitFor();
  await page.locator('[data-id="repeat"] .source[data-handleid="4d_cams"]').waitFor();
  await page.waitForTimeout(700);
  await page.screenshot({path:path.join(here,`ports-${theme}.png`)});
 }
 if(errors.length)throw Error(errors.join('\n'));
 console.log('Light/dark real canvas ports verified; no page errors.');
}finally{await browser?.close();await server.close();}
