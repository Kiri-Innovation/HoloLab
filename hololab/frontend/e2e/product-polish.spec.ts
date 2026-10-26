import {test, expect} from '@playwright/test';
import {readFile} from 'node:fs/promises';

const pack={name:'polish/train',version:'1.0',description:'Train a scene',inputs:{},outputs:{},params:{steps:{type:'int',default:12}},node_ids:['compute'],arrayable:false};
const initial={nodes:[{id:'train',kind:'algorithm',algorithm_name:pack.name,algorithm_version:pack.version,position:{x:300,y:200},params:{steps:12},assigned_node_id:'compute',param_bindings:{steps:'iterations'}}],edges:[],variables:{iterations:{type:'int',default:12,description:'Training steps'}},variable_values:{iterations:24}};

test('recipe bytes, import navigation, variable rename/binding and capability contract',async({page},info)=>{
 let graph:any=structuredClone(initial); const analyses:any[]=[]; const capabilities:any[]=[];
 const recipe='{"schema_version":1,"name":"Byte-preserved recipe","graph":{"nodes":[],"edges":[]}}\n';
 let imported=''; let saved:any;
 page.on('pageerror',error=>{throw error;});
 await page.routeWebSocket('**',()=>{});
 await page.route('**/api/**',async route=>{
  const path=new URL(route.request().url()).pathname;
  let body:any=[];
  if(path==='/api/pack-catalog')body=[pack];
  else if(path==='/api/nodes')body=[{node_id:'compute',node_name:'Compute',gpu:{count:0},packs:[pack]}];
  else if(path==='/api/nodes/metrics/history')body={nodes:{}};
  else if(path==='/api/workflows/polish'||path==='/api/workflows/imported')body={workflow_id:path.split('/').at(-1),name:'Polish review',graph,updated_ts:1,created_ts:1};
  else if(path==='/api/workflows'&&route.request().method()==='POST'){saved=route.request().postDataJSON();graph=saved.graph;body={workflow_id:'polish',updated_ts:2};}
  else if(path.endsWith('/analyze')){const data=route.request().postDataJSON();analyses.push(data.graph);const g=data.graph; const n=g.nodes[0];const key=n.param_bindings?.steps;body={revision:'current',graph:{...g,nodes:[{...n,params:{steps:key ? g.variable_values[key]??g.variables[key]?.default : n.params.steps}}]},staleness:{train:null},diff:[],environment_params:{}};}
  else if(path==='/api/capabilities/check'){capabilities.push(route.request().postDataJSON());body={nodes:{train:{status:'missing',source:'polish/train@1.0',node_id:'compute',issues:[{item:'package:demo',required:'>=2',actual:'未安装',remedy:'安装依赖后重新检查'}]}}};}
  else if(path==='/api/recipes/check')body={issues:[{node_id:'train',code:'missing_parameter',message:'参数 steps 未提供值'}],environment:{}};
  else if(path.endsWith('/recipe'))return route.fulfill({body:recipe,contentType:'application/json'});
  else if(path==='/api/recipes/import'){imported=route.request().postData()!;body={workflow_id:'imported',issues:[]};}
  await route.fulfill({json:body});
 });
 await page.setViewportSize({width:1440,height:1000});
 await page.goto('/w/polish');
 await page.getByRole('button',{name:'定位节点 train'}).click();
 const variables=page.locator('.hl-workflow-variables');
 await variables.locator('summary').click();
 await page.getByLabel('iterations 本地值').fill('48');
 await variables.getByRole('button',{name:'改名',exact:true}).click();
 await page.getByLabel('iterations 新名称').fill('training_steps');
 await page.getByRole('button',{name:'保存名称',exact:true}).click();
 await expect(page.getByLabel('steps 绑定变量')).toHaveValue('training_steps');
 await expect.poll(()=>analyses.at(-1)?.variable_values?.training_steps).toBe(48);
 await expect.poll(()=>analyses.at(-1)?.nodes[0]?.param_bindings?.steps).toBe('training_steps');
 await page.locator('.hl-variable-parameter summary').click();
 await expect(page.locator('.hl-variable-parameter')).toContainText('48');
 await page.locator('.hl-recipe-menu > summary').click();
 const download=page.waitForEvent('download');
 await page.getByRole('button',{name:'导出配方',exact:true}).click();
 expect(await readFile(await (await download).path() as string, 'utf8')).toBe(recipe);
 expect(saved.graph.variables.training_steps.type).toBe('int');
 expect(saved.graph.variable_values.training_steps).toBe(48);
 expect(saved.graph.nodes[0].params.steps).toBe(12);
 expect(capabilities.at(-1).graph).toEqual({nodes:[{id:'train',kind:'algorithm',algorithm_name:pack.name,algorithm_version:pack.version,assigned_node_id:'compute'}],edges:[]});
 for(const theme of ['light','dark']){
  await page.evaluate(t=>document.documentElement.dataset.theme=t,theme);
  await page.screenshot({animations:'disabled',path:info.outputPath(`workflow-${theme}.png`),fullPage:true});
 }
 await page.getByLabel('从配方文件新建工作流').setInputFiles({name:'recipe.json',mimeType:'application/json',buffer:Buffer.from(recipe)});
 await expect(page).toHaveURL(/\/w\/imported$/);
 expect(imported).toBe(recipe);
 await expect(page.getByRole('button',{name:'定位节点 train'})).toBeVisible();
});

test('destructive actions require confirmation and internal errors stay hidden',async({page})=>{
 await page.route('**/api/**',route=>route.fulfill({status:500,body:'Traceback: /private/secret.py database password'}));
 await page.goto('/reports/product-polish/index.html');
 const variables=page.locator('.hl-workflow-variables');await variables.locator(':scope > summary').click();
 page.once('dialog',dialog=>dialog.dismiss());
 await variables.getByRole('button',{name:'删除声明'}).first().click();
 await expect(page.getByLabel('dataset 默认值')).toBeVisible();
 page.once('dialog',dialog=>dialog.accept());
 await variables.getByRole('button',{name:'删除声明'}).first().click();
 await expect(page.getByLabel('dataset 默认值')).toHaveCount(0);
 await page.getByRole('button',{name:'导出配方',exact:true}).click();
 await expect(page.getByRole('alert')).toContainText('未能导出');
 await expect(page.locator('body')).not.toContainText('secret.py');
 await page.locator('[data-shot=identity] > details > summary').click();
 await expect(page.locator('[data-shot=identity] pre')).toHaveCount(0);
});


test('identity raw evidence requires the debug switch',async({page})=>{
 await page.goto('/reports/product-polish/index.html?debug=1');
 await page.locator('[data-shot=identity] > details > summary').click();
 await page.getByText('完整冻结记录（调试）',{exact:true}).click();
 await expect(page.locator('[data-shot=identity] pre')).toContainText('"verification": "verified_at_submission"');
});
