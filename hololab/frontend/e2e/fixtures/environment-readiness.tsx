import React from 'react';
import { createRoot } from 'react-dom/client';
import { ReactFlow, ReactFlowProvider } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import '../../src/styles.css';
import { AlgorithmNode } from '../../src/canvas/AlgorithmNode';
import { CapabilityContext } from '../../src/canvas/EnvironmentChecks';
import { CanvasContext } from '../../src/canvas/CanvasContext';
import { TooltipLayer } from '../../src/ui/TooltipLayer';

const pack={node_ids:[],manifest_hash:'fixture',description:'',category:[],name:'check-pack',version:'1.0.0',inputs:{},outputs:{out:{tags:['image']}},params:{},arrayable:false};
const missing={status:'missing' as const,source:'check-pack@1.0.0',issues:[{item:'package:demo',required:'demo>=2',actual:'未安装',remedy:'在所选环境安装 demo 后重新检查'}]};
const cases=[
  ['missing',undefined], ['normal','done'], ['running','running'], ['unknown',undefined], ['undeclared',undefined], ['upstream',undefined],
] as const;
const query=new URLSearchParams(location.search);
document.documentElement.dataset.theme=query.get('theme') || 'light';
const nodes=cases.map(([id,state],index)=>({id,type:'algorithm',position:{x:80+(index%3)*300,y:130+Math.floor(index/3)*250},data:{pack,assigned_node_id:null,runtime:state?{state,job_id:state==='running'?'running-job':undefined}:undefined}}));
const reports={missing, running:missing, unknown:{status:'unknown' as const,issues:[]}, undeclared:{status:'undeclared' as const,issues:[]}, upstream:missing};
const context={workflow_id:null,workflow_name:'fixture',computeNodesById:{},packDisplayName:(name:string)=>name,hydrating:false,viewer_view:null,viewer_view_loaded:false,viewer_view_managed:false,upstreamBlockerByNode:{upstream:'上游节点 source 尚未运行；请先运行上游。'}};
createRoot(document.getElementById('root')!).render(<ReactFlowProvider><CanvasContext.Provider value={context}><CapabilityContext.Provider value={reports}><ReactFlow nodes={nodes} edges={[]} nodeTypes={{algorithm:AlgorithmNode}} defaultViewport={{x:0,y:0,zoom:1}}/></CapabilityContext.Provider></CanvasContext.Provider><TooltipLayer/></ReactFlowProvider>);
