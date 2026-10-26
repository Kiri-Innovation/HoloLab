import {test, expect, type Page} from '@playwright/test';
import {mkdirSync} from 'node:fs';
const directory='reports/pack-manager/screenshots';
const train={name:'studio/train',version:'1.0.0',manifest_hash:'train',node_ids:['gpu'],description:'Train a scene',inputs:{input:{tags:['number'],storage:'value',required:false}},outputs:{out:{tags:['number'],storage:'value'}},params:{steps:{type:'int',default:12}},requires:{cuda: '>=12'},publication:{source:'https://registry.example.test/packs',yanked:false}};
const load={...train,name:'studio/load',manifest_hash:'load',requires:null,publication:null};
function graph(missing:boolean){return {nodes:[{id:'train',kind:'algorithm',algorithm_name:train.name,algorithm_version:train.version,assigned_node_id:'gpu',params:{steps:12},param_bindings:{steps:'iterations'},position:{x:100,y:100}},
 {id:'load',kind:'algorithm',algorithm_name:missing?'external/missing':load.name,algorithm_version:'1.0.0',assigned_node_id:'gpu',params:{},position:{x:440,y:100}}],edges:missing?[]:[{id:'link',source:'train',sourceHandle:'out',target:'load',targetHandle:'input'}],variables:{iterations:{type:'int',default:12}},variable_values:{iterations:24}};}
async function setup(page:Page, scenario:'missing'|'healthy'|'empty'='missing'){
 const checks:any[]=[]; const recipes:any[]=[]; let runs=0;
 const missing=scenario==='missing';let draft:any=graph(missing);
 const packs=scenario==='empty'?[]:[train,load];
 await page.routeWebSocket('**',()=>{});
 await page.route('https://registry.example.test/packs/index.json',route=>route.fulfill({json:{format:1,releases:{
  'external/missing@1.0.0':{name:'external/missing',version:'1.0.0',yanked:false},
  'extra/retired@2.0.0':{name:'extra/retired',version:'2.0.0',yanked:true},
 }}}));
 await page.route('**/api/**',async route=>{
  const path=new URL(route.request().url()).pathname; let body:any=[];
  if(path==='/api/pack-catalog')body=packs;
  else if(path==='/api/packs')body=packs.map(p=>({name:p.name,version:p.version,node_id:'gpu',node_name:'Studio GPU',online:true}));
  else if(path==='/api/nodes')body=scenario==='empty'?[]:[{node_id:'gpu',node_name:'Studio GPU',gpu:{count:1},packs,workspace_root:'/data'}];
  else if(path==='/api/nodes/metrics/history')body={nodes:{}};
  else if(path==='/api/workflows'&&route.request().method()==='GET')body=scenario==='empty'?[]:[{workflow_id:'scene',name:'Scene capture',node_count:2}];
  else if(path==='/api/workflows/scene')body={workflow_id:'scene',name:'Scene capture',graph:draft,created_ts:1,updated_ts:1};
  else if(path==='/api/workflows'&&route.request().method()==='POST'){draft=route.request().postDataJSON().graph;body={workflow_id:'scene',updated_ts:2};}
  else if(path.endsWith('/analyze')){const g=route.request().postDataJSON().graph;body={revision:'ok',graph:g,staleness:{},diff:[],environment_params:{}};}
  else if(path==='/api/recipes/check') {recipes.push(route.request().postDataJSON());body={issues:missing?[{node_id:'load',code:'missing_package',message:'包未发现'},{node_id:'train',code:'resolution',message:'private implementation error'}]:[{node_id:'load',code:'environment',message:'环境未声明'}],environment:{}};}
  else if(path==='/api/capabilities/check') {
   const request=route.request().postDataJSON();checks.push(request);
   body={nodes:Object.fromEntries(request.graph.nodes.map((n:any)=>[n.id,n.algorithm_name==='studio/train'?{status:missing?'missing':'satisfied',source:`${n.algorithm_name}@${n.algorithm_version}`,node_id:'gpu',issues:missing?[{item:'cuda',required:'>=12',actual:'11.8',remedy:'选择 CUDA 12 或更高版本的计算节点。'}]:[]}:{status:'undeclared',issues:[]}]))};
  } else if(path.endsWith('/pack-status'))body={registry_status:'published',state:'installed'};
  else if(path.endsWith('/prepare'))body={token:'prepared'};
  else if(path.endsWith('/run')){runs++;body={workflow_id:'scene',snapshot_id:null,node_count:2};}
  await route.fulfill({json:body});
 });
 return {checks,recipes,get runs(){return runs;}};
}

for(const scenario of ['missing','healthy','empty'] as const) for(const theme of ['light','dark']) test(`Pack Manager ${scenario} ${theme}`,async({page})=>{
 mkdirSync(directory,{recursive:true}); const requests=await setup(page,scenario);
 page.on('pageerror',error=>{throw error;});
 await page.addInitScript(t=>localStorage.setItem('hololab.theme',t),theme);
 await page.setViewportSize({width:1440,height:1000});
 await page.goto('/packs');
 await expect(page.getByRole('heading',{name:'包管理',exact:true})).toBeVisible();
 await expect(page.getByRole('button',{name:'刷新全部状态'})).toBeEnabled();
 if(scenario==='empty')await expect(page.getByText('还没有发现包。',{exact:false})).toBeVisible();
 else {
  await expect.poll(()=>requests.checks.length).toBeGreaterThan(0);
  await expect(page.locator('.hl-pack-summary')).not.toContainText('环境检查中');
  expect(requests.recipes[0]).toEqual({graph:graph(scenario==='missing')});
  for(const node of requests.checks.at(-1).graph.nodes)expect(Object.keys(node).sort()).toEqual(['algorithm_name','algorithm_version','assigned_node_id','id','kind']);
  if(scenario==='missing'){
   await expect(page.locator('[data-pack-key="studio/train@1.0.0"]')).toContainText('11.8');
   const row=page.locator('[data-pack-key="studio/train@1.0.0"]');await row.getByText('使用情况与配方检查',{exact:false}).click();
   await expect(row).toContainText('Scene capture');await expect(row).toContainText('变量或参数无法解析');
   await expect(page.locator('body')).not.toContainText('private implementation');
  } else {
   await page.getByLabel('只看有问题的').check();
   await expect(page.locator('.hl-pack-card')).toHaveCount(0);
   await page.getByLabel('只看有问题的').uncheck();
  }
 }
 await page.screenshot({path:`${directory}/manager-${scenario}-${theme}.png`,fullPage:true,animations:'disabled'});
 await page.setViewportSize({width:390,height:844});
 await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await page.screenshot({path:`${directory}/manager-${scenario}-${theme}-narrow.png`,fullPage:true,animations:'disabled'});
});

test('registry-only releases, yank, command copying and page navigation',async({page})=>{
 await setup(page);await page.goto('/packs/');
 await page.getByText('注册表目录 · 尚未加载',{exact:true}).click();
 await page.getByLabel('注册表目录 URL').fill('https://registry.example.test/packs');
 await page.getByRole('button',{name:'加载注册表',exact:true}).click();
 const row=page.locator('[data-pack-key="extra/retired@2.0.0"]');await expect(row).toContainText('已 yank');
 await row.getByText('安装与源码',{exact:true}).click();
 await expect(row.getByRole('button',{name:'复制安装命令'})).toHaveCount(0);
 const missing=page.locator('[data-pack-key="external/missing@1.0.0"]');
 await missing.getByText('安装与源码',{exact:true}).click();
 await page.evaluate(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async(text:string)=>{(window as any).__command=text;}}}));
 await missing.getByRole('button',{name:'复制安装命令'}).click();
 await expect.poll(()=>page.evaluate(()=>(window as any).__command)).toBe("hololab pack install 'external/missing@1.0.0' --source 'https://registry.example.test/packs'");
 await expect(missing).toContainText('尚未执行安装');
 await page.getByRole('button',{name:'Library',exact:true}).click();await expect(page).toHaveURL(/\/library$/);
 await page.goBack();await expect(page).toHaveURL(/\/packs\/$/);
 await page.reload();await expect(page.getByRole('heading',{name:'包管理',exact:true})).toBeVisible();
});

for(const theme of ['light','dark'])test(`clean canvas still checks, edits, runs and frames nodes ${theme}`,async({page})=>{
 const requests=await setup(page,'healthy');
 await page.addInitScript(t=>localStorage.setItem('hololab.theme',t),theme);
 await page.setViewportSize({width:1440,height:1000});await page.goto('/w/scene');
 await expect(page.locator('[data-hololab-node="algorithm"]')).toHaveCount(2);
 await expect(page.locator('.react-flow__edge')).toHaveCount(1);
 await expect.poll(()=>requests.checks.length).toBeGreaterThan(0);await expect.poll(()=>requests.recipes.length).toBeGreaterThan(0);
 await expect(page.locator('.hl-workflow-checks,.hl-recipe-problems,.hl-capability-panel')).toHaveCount(0);
 await expect(page.locator('.react-flow')).not.toContainText('环境未声明');
 await expect(page.locator('.react-flow')).not.toContainText('发布状态');
 await expect(page.locator('.react-flow [data-hl-open-source]')).toHaveCount(0);
 await page.locator('[data-id="train"] [data-hololab-node="algorithm"]').click();
 await expect(page.locator('.hl-node-inspector')).toBeVisible();
 await expect(page.locator('.hl-inspector-package-field')).toHaveCount(0);
 await page.getByLabel('steps 输入模式').selectOption('inline');
 await page.getByLabel('steps',{exact:true}).fill('36');
 await page.evaluate(()=>(document.activeElement as HTMLElement)?.blur());await page.keyboard.press('f');
 await page.locator('.hl-recipe-menu > summary').click();await page.getByRole('button',{name:'保存配方文件',exact:true}).click();
 await page.keyboard.press('Escape');await page.getByRole('button',{name:'运行流程',exact:true}).click();
 await expect.poll(()=>requests.runs).toBe(1);
 await page.getByRole('tab',{name:'Jobs',exact:true}).click();await expect(page.getByRole('tabpanel',{name:'Jobs',exact:true})).toBeVisible();
 await page.getByRole('tab',{name:'Compute Nodes · Pulse',exact:true}).click();
 await page.getByRole('tab',{name:'Runs',exact:true}).click();await expect(page.locator('#hl-dock-panel-runs')).toBeVisible();
 await page.getByRole('tab',{name:'Inspector',exact:true}).click();
 await page.screenshot({path:`${directory}/canvas-${theme}.png`,fullPage:true,animations:'disabled'});
 await page.setViewportSize({width:390,height:844});
 await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await page.screenshot({path:`${directory}/canvas-${theme}-narrow.png`,fullPage:true,animations:'disabled'});
 await page.setViewportSize({width:1440,height:1000});
 await page.getByRole('button',{name:'Pack Manager',exact:true}).click();await expect(page).toHaveURL(/\/packs$/);
});

test('partial data and status failures remain honest and hide internal errors',async({page})=>{
 await setup(page,'healthy');
 await page.route('**/api/workflows',route=>route.fulfill({status:500,body:'private traceback'}));
 await page.route('**/api/nodes/gpu/pack-status?*',route=>route.fulfill({status:500,body:'private traceback'}));
 await page.goto('/packs');
 await expect(page.getByText('工作流列表未能读取，当前清单可能不完整。')).toBeVisible();
 const row=page.locator('[data-pack-key="studio/train@1.0.0"]');
 await row.getByRole('button',{name:'查询当前发布状态'}).click();
 await expect(row).toContainText('查询失败：');
 await expect(page.locator('body')).not.toContainText('private traceback');
 await page.route('**/api/nodes/gpu/pack-status?*',route=>route.fulfill({json:{registry_status:'yanked',state:'installed'}}));
 await row.getByRole('button',{name:'查询当前发布状态'}).click();
 await expect(row).toContainText('已 yank · 禁止新安装');
 await row.getByText('安装与源码',{exact:true}).click();
 await expect(row.getByRole('button',{name:'复制安装命令'})).toHaveCount(0);
});
