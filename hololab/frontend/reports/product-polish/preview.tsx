import React, {useState} from 'react';
import {createRoot} from 'react-dom/client';
import '../../src/styles.css';
import '../../src/canvas/inspector.css';
import {RecipeFileActions} from '../../src/canvas/RecipeFileActions';
import {WorkflowVariables} from '../../src/canvas/WorkflowVariables';
import {EnvironmentChecks} from '../../src/canvas/EnvironmentChecks';
import {CodeIdentityPanel} from '../../src/canvas/CodeIdentityPanel';
import {SnapshotBanner} from '../../src/canvas/SnapshotBanner';
import {NodeInspector} from '../../src/canvas/NodeInspector';
const digest='a91d3f87b5c002ea70c14d31e6079828d350f75a49d7631d22701f3912a2c431';
const report={status:'missing',source:'demo/train@1.0',node_id:'workstation',checked_at:1793000000,issues:[{item:'package:mmcv',required:'>=2.0',actual:'未安装',remedy:'安装声明版本的 mmcv，然后重新检查。'},{item:'cuda',required:'>=12',actual:'11.8',remedy:'请使用满足要求的计算节点。'}]};
const identity={format:2,source:'demo/train@1.0',sha256:digest,hot:{sha256:digest,verification:'verified_at_submission'},cold:{sha256:digest,baseline_at:1793000000,baseline_status:'updated',content_changed:false},manifest:{sha256:digest,raw_sha256:digest},controls:{packignore_sha256:digest},git:{dirty:true,commit:digest,tree_sha:digest},ignored:{files:82,links:2,bytes:2048000,top_level_names:['assets','checkpoints']},uncovered:[{path:'/opt/native/extensions/renderer.so',declared:true,covered:false,role:'runtime'}],undeclared:[{path:'/workspace/external/helper.py',declared:false}],effective_params:{iterations:12000,dataset:'/data/capture/scene-01'},environment:report};
const pack={name:'demo/train',version:'1.0',description:'训练场景并保存结果。',inputs:{},outputs:{},params:{iterations:{type:'int',default:3000},dataset:{type:'path',default:'/data'}},node_ids:['workstation']};
function App(){
 const [variables,setVariables]=useState({dataset:{type:'path',description:'输入数据目录',default:'/data/capture'},steps:{type:'int',description:'训练步数',default:3000}});
 const [values,setValues]=useState({steps:12000});
 const [selected,setSelected]=useState({id:'train',kind:'algorithm',algorithm_name:pack.name,algorithm_version:pack.version,params:{iterations:3000,dataset:'/data'},param_bindings:{iterations:'steps'},position:{x:0,y:0}});
 return <main style={{padding:24,maxWidth:1200,margin:'auto',display:'grid',gap:24}}>
 <section data-shot="recipe"><h2>配方文件</h2><RecipeFileActions workflowId="demo" onOpen={()=>{}} onSave={async()=>{}} onReload={async()=>{}} /></section>
 <section data-shot="snapshot" style={{position:'relative',height:160}}><SnapshotBanner snapshot={{snapshot_id:'snapshot-demo',workflow_id:'demo',created_ts:1793000000,jobs:[],code_evidence:{},variable_evidence:{values:{steps:12000}}} as any} onBack={()=>{}} onRestored={()=>{}} /></section>
 <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(min(100%,340px),1fr))',gap:24}}>
 <section data-shot="variables"><WorkflowVariables variables={variables as any} values={values} onChange={(v,x)=>{setVariables(v as any);setValues(x as any)}} /><NodeInspector selected={selected as any} pack={pack as any} variables={variables as any} effectiveParams={{iterations:12000,dataset:'/data'}} onChange={p=>setSelected({...selected,...p} as any)} onParamsChange={p=>setSelected({...selected,params:{...selected.params,...p}} as any)} /></section>
 <section data-shot="environment" style={{position:'relative',minHeight:640}}><EnvironmentChecks checks={{reports:{train:report as any,loader:{status:'undeclared',source:'demo/load@1',issues:[]}},loading:false,error:'',refresh:()=>{}}} /></section></div>
 <section data-shot="identity"><CodeIdentityPanel detail={{code_identity:identity,node_id:'workstation',algorithm_name:pack.name,algorithm_version:pack.version} as any}/></section>
 </main>;
}
createRoot(document.getElementById('root')!).render(<App/>);
