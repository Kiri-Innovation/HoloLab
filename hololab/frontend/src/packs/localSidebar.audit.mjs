// Run against Vite: node src/packs/localSidebar.audit.mjs. API traffic is mocked.
import {chromium, expect} from '@playwright/test';
import {mkdir, writeFile} from 'node:fs/promises';
const output=process.env.PACK_AUDIT_OUTPUT || '/tmp/pack-sidebar-audit';
await mkdir(output,{recursive:true});
const browser=await chromium.launch({headless:true});
const evidence=[];
const packs=[
 {name:'colmap-sfm',version:'0.4.2',category:['reconstruction','colmap'],requires:{cuda:'>=12'}},
 {name:'image-tools',version:'1.0.0',category:['utility'],requires:null},
 {name:'colmap-other',version:'1.0.0',category:['reconstruction','colmap-extra'],requires:null},
].map(p=>({...p,node_ids:['local'],manifest_hash:p.name,inputs:{},outputs:{},params:{}}));
try {
 for(const theme of ['light','dark']) {
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  await page.addInitScript(t=>localStorage.setItem('hololab.theme',t),theme);
  await page.routeWebSocket('**',()=>{});
  let empty=false, fail=false, capabilityFail=false;
  await page.route('**/api/**',async route=>{
   const path=new URL(route.request().url()).pathname;
   if((fail&&path==='/api/pack-catalog')||(capabilityFail&&path==='/api/capabilities/check'))return route.fulfill({status:500,json:{}});
   let json=[];
   if(path==='/api/pack-catalog')json=empty?[]:packs;
   if(path==='/api/nodes')json=[{node_id:'local',node_name:'本机',packs:[],gpu:{count:0}}];
   if(path==='/api/capabilities/check')json={nodes:Object.fromEntries(route.request().postDataJSON().graph.nodes.map(n=>[n.id,{status:n.algorithm_name==='kiri/colmap-sfm'?'missing':'undeclared',issues:n.algorithm_name==='kiri/colmap-sfm'?[{item:'cuda',required:'>=12',actual:'11.8'}]:[]}]))};
   await route.fulfill({json});
  });
  const sidebar=page.locator('#pack-panel-local aside');
  const tree=page.getByRole('navigation',{name:'本地包目录'});
  const content=page.getByRole('region',{name:'本地包内容'});
  async function check(label, screenshot=false) {
   await expect(tree).toBeVisible();
   await expect(sidebar.locator('[aria-current]')).toHaveCount(1);
   evidence.push({theme,label,url:page.url(),current:await sidebar.locator('[aria-current]').innerText(),count:await sidebar.locator('[aria-current]').count()});
   if(screenshot)await page.screenshot({path:`${output}/${theme}-${label}.png`,fullPage:true});
  }
  await page.goto('http://127.0.0.1:5173/packs');
  await expect(sidebar.getByRole('button',{name:'需要注意 (1)',exact:true})).toBeVisible();
  await expect(sidebar.getByRole('button',{name:'功能分类',exact:true})).toHaveCount(0);
  await check('overview',true);
  await sidebar.getByRole('button',{name:'环境缺失 (1)',exact:true}).click();
  await expect(content.locator('.hl-pack-project-link')).toHaveCount(1);
  await expect(content).toContainText('缺少环境：cuda');await check('missing',true);
  await tree.getByRole('button',{name:'colmap',exact:true}).click();
  await expect(content.getByRole('heading',{name:'reconstruction / colmap',exact:true})).toBeVisible();
  await expect(content.locator('.hl-pack-project-link')).toHaveCount(1);await check('category',true);
  await tree.getByRole('button',{name:'reconstruction',exact:true}).click();
  await expect(content.locator('.hl-pack-project-link')).toHaveCount(2);
  await tree.getByRole('button',{name:'colmap',exact:true}).click();
  await page.reload();await check('category-reload');
  await tree.getByRole('button',{name:/colmap-sfm/}).click();
  await expect(content.locator('[data-pack-key="kiri/colmap-sfm@0.4.2"]')).toContainText('缺少环境：cuda');await check('package',true);
  await page.reload();await check('package-reload');
  await sidebar.getByRole('button',{name:'未声明 (2)',exact:true}).click();
  await expect(content.locator('.hl-pack-project-link')).toHaveCount(2);await check('undeclared');
  await tree.getByRole('button',{name:'收起 reconstruction',exact:true}).click();
  await page.goBack();await expect(content.locator('[data-pack-key]')).toBeVisible();await check('back-package');
  await page.goBack();await expect(content.getByRole('heading',{name:'reconstruction / colmap',exact:true})).toBeVisible();await check('back-category');
  await page.goForward();await expect(content.locator('[data-pack-key]')).toBeVisible();await check('forward-package');
  await page.goForward();await expect(content.getByRole('heading',{name:'未声明环境要求',exact:true})).toBeVisible();await check('forward-undeclared');
  await sidebar.getByRole('button',{name:'需要注意 (1)',exact:true}).click();await check('attention');
  await page.getByRole('searchbox',{name:'搜索包',exact:true}).fill('no-such-package');
  await expect(content).toContainText('没有符合筛选条件的包');await expect(tree).toBeVisible();
  await sidebar.getByRole('button',{name:'总览',exact:true}).click();
  await expect(page.getByRole('searchbox',{name:'搜索包',exact:true})).toHaveValue('');
  await page.setViewportSize({width:390,height:844});await check('narrow',true);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  empty=true;await page.reload();await expect(content).toContainText('还没有发现包');await check('empty');
  empty=false;fail=true;await page.reload();await expect(content).toContainText('当前包目录未能读取');
  fail=false;await sidebar.getByRole('button',{name:'刷新',exact:true}).click();await expect(tree.getByRole('button',{name:'reconstruction',exact:true})).toBeVisible();
  capabilityFail=true;await page.reload();await expect(content.getByRole('button',{name:'重试环境检查'})).toBeVisible();
  capabilityFail=false;await content.getByRole('button',{name:'重试环境检查'}).click();await expect(sidebar.getByRole('button',{name:'环境缺失 (1)',exact:true})).toBeVisible();await check('retry');
  await page.close();
 }
 await writeFile(`${output}/evidence.json`,JSON.stringify(evidence,null,2));
 console.log(`PASS: ${evidence.length} unique-selection/persistent-tree checks; screenshots and URL evidence: ${output}`);
} finally {await browser.close();}
