// Run against Vite: node src/packs/localSidebar.audit.mjs. All API traffic is mocked.
import {chromium, expect} from '@playwright/test';
import {mkdir, writeFile} from 'node:fs/promises';
const output=process.env.PACK_AUDIT_OUTPUT || '/tmp/pack-pages-audit';
const base=process.env.PACK_AUDIT_URL || 'http://127.0.0.1:5173';
await mkdir(output,{recursive:true});
const browser=await chromium.launch({headless:true});
const evidence=[];
const pack=(name,category,requires=null,date='2026-10-25T12:00:00Z')=>({name,version:'0.4.2',category,requires,node_ids:['local'],manifest_hash:name,inputs:{},outputs:{},params:{},description:'从输入图像重建相机与稀疏点云。',publication:date?{published_at:date}:null});
try {
 for(const theme of ['light','dark']) for(const scenario of ['unique','collision']) {
  const page=await browser.newPage({viewport:{width:1440,height:1100}});
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.addInitScript(t=>localStorage.setItem('hololab.theme',t),theme);
  await page.routeWebSocket('**',()=>{});
  const packs=[pack('kiri/colmap-sfm',['reconstruction','colmap'],{cuda:'>=12'}),pack('kiri/image-tools',['utility'],null,null),pack('kiri/colmap-other',['reconstruction','colmap-extra'],null,'2026-10-20T12:00:00Z')];
  if(scenario==='collision')packs.push(pack('community/colmap-sfm',['utility']));
  // Community has its own collision domain: even the unique local fixture has remote duplicates.
  const community=[...packs.filter(p=>p.name!=='community/colmap-sfm'),pack('community/colmap-sfm',['utility'])];
  const requests=[];let empty=false,fail=false,capabilityFail=false,hold;
  await page.route('**/api/**',async route=>{
   const url=new URL(route.request().url()),path=url.pathname;
   requests.push({path,query:url.search,body:route.request().postData()});
   if(hold&&path==='/api/pack-catalog')await hold;
   if((fail&&path==='/api/pack-catalog')||(capabilityFail&&path==='/api/capabilities/check'))return route.fulfill({status:500,json:{}});
   let json=[];
   if(path==='/api/pack-catalog')json=empty?[]:packs;
   if(path==='/api/nodes')json=[{node_id:'local',node_name:'本机',packs:[],gpu:{count:0}}];
   if(path==='/api/capabilities/check')json={nodes:Object.fromEntries(route.request().postDataJSON().graph.nodes.map(n=>[n.id,{status:n.algorithm_name==='kiri/colmap-sfm'?'missing':'undeclared',issues:n.algorithm_name==='kiri/colmap-sfm'?[{item:'cuda',required:'>=12',actual:'11.8'}]:[]}]))};
   if(path==='/api/packs/registry'){
    const name=url.searchParams.get('name');
    json=name?{releases:{latest:{...community.find(p=>p.name===name),published_at:'2026-10-25T12:00:00Z',publisher:'Studio',code_sha256:'a'.repeat(64),yanked:false}}}:{projects:community.map(p=>({name:p.name}))};
   }
   if(path==='/api/packs/install')json={installed:true};
   if(path.endsWith('/pack-status'))json={registry_status:'published',state:'installed'};
   await route.fulfill({json});
  });
  const sidebar=page.locator('#pack-panel-local aside');
  const tree=page.getByRole('navigation',{name:'本地包目录'});
  const content=page.getByRole('region',{name:'本地包内容'});
  const label=scenario==='collision'?'kiri/colmap-sfm':'colmap-sfm';
  async function ready(){await expect(sidebar.getByRole('button',{name:'环境缺失 (1)',exact:true})).toBeVisible();}
  async function check(kind,phase,screenshot=false) {
   await ready();await expect(tree).toBeVisible();
   await expect(sidebar.locator('[aria-current]')).toHaveCount(1);
   await expect(content.locator('[data-local-page]')).toHaveAttribute('data-local-page',kind);
   await expect(content.getByRole('searchbox')).toHaveCount(kind==='overview'?1:0);
   await expect(content.getByRole('heading',{level:1})).toHaveCount(1);
   expect(await content.evaluate(el=>el.scrollTop)).toBe(0);
   if(kind==='package') {
    await expect(content.getByRole('heading',{level:1})).toHaveText(label);
    await expect(sidebar.locator('[aria-current] > span').first()).toHaveText(label);
    await expect(content.getByRole('navigation',{name:'包路径'})).toContainText(label);
    const widths=await content.evaluate(el=>({page:el.querySelector('[data-local-page]').getBoundingClientRect().width,available:el.clientWidth-parseFloat(getComputedStyle(el).paddingLeft)-parseFloat(getComputedStyle(el).paddingRight)}));
    expect(Math.abs(widths.page-widths.available)).toBeLessThan(2);
    await expect(page.locator('dialog[open]')).toHaveCount(0);
   }
   evidence.push({theme,scenario,kind,phase,url:page.url(),current:await sidebar.locator('[aria-current]').innerText(),selectedCount:1,searchCount:await content.getByRole('searchbox').count()});
   if(screenshot){await page.mouse.move(1400,20);await page.screenshot({path:`${output}/${theme}-${scenario}-${kind}.png`,fullPage:true,animations:'disabled'});}
  }
  await page.goto(`${base}/packs`);
  const views=[
   ['overview',async()=>{}],
   ['attention',()=>sidebar.getByRole('button',{name:'需要注意 (1)',exact:true}).click()],
   ['missing',()=>sidebar.getByRole('button',{name:'环境缺失 (1)',exact:true}).click()],
   ['undeclared',()=>sidebar.getByRole('button',{name:`未声明 (${packs.length-1})`,exact:true}).click()],
   ['category',()=>tree.getByRole('button',{name:'colmap',exact:true}).click()],
   ['package',()=>tree.locator('.hl-pack-leaf').filter({has:page.locator('span',{hasText:new RegExp(`^${label}$`)})}).click()],
  ];
  for(let i=0;i<views.length;i++) {
   const [kind,select]=views[i];await select();await check(kind,'selected',true);
   const url=page.url();await page.reload();await check(kind,'reload');expect(page.url()).toBe(url);
   if(i>0){await page.goBack();await check(views[i-1][0],'back');await page.goForward();await check(kind,'forward');expect(page.url()).toBe(url);}
  }
  await sidebar.getByRole('button',{name:'总览',exact:true}).click();
  await page.goBack();await check('package','back-from-overview');
  await page.goForward();await check('overview','forward-from-package');
  await page.goBack();
  await expect(content).toContainText('缺少环境：cuda');
  await content.getByText('安装与源码',{exact:true}).click();
  await expect(content.locator('.hl-pack-command')).toContainText('kiri/colmap-sfm@0.4.2');
  await content.getByRole('button',{name:'查询当前发布状态',exact:true}).click();
  await expect.poll(()=>requests.some(r=>r.path.endsWith('/pack-status')&&new URLSearchParams(r.query).get('name')==='kiri/colmap-sfm')).toBe(true);
  await sidebar.getByRole('button',{name:'总览',exact:true}).click();
  await content.getByRole('searchbox').fill('kiri/colmap-sfm');
  await expect(content.locator('.hl-pack-project-link')).toHaveText(label);
  await page.screenshot({path:`${output}/${theme}-${scenario}-search.png`,fullPage:true,animations:'disabled'});
  // A hidden overview query must not narrow the status page or the category tree.
  await sidebar.getByRole('button',{name:`未声明 (${packs.length-1})`,exact:true}).click();
  await expect(content.locator('.hl-pack-project-link')).toHaveCount(packs.length-1);
  await tree.getByRole('button',{name:'colmap',exact:true}).click();
  await expect(content.locator('.hl-pack-project-link')).toHaveText(label);
  await expect(content).not.toContainText('colmap-other');
  await tree.getByRole('button',{name:'reconstruction',exact:true}).click();
  await expect(content.locator('.hl-pack-project-link')).toHaveCount(2);
  // Community labels use registry names, not the local catalog or filtered results.
  await page.getByRole('tab',{name:'社区',exact:true}).click();
  const remote=page.getByRole('region',{name:'社区包',exact:true});
  await expect(remote.locator('.hl-community-card')).toHaveCount(4);
  await expect(remote.locator('[data-project="kiri/colmap-sfm"] strong')).toHaveText('kiri/colmap-sfm');
  await expect(remote.locator('[data-project="community/colmap-sfm"] strong')).toHaveText('community/colmap-sfm');
  await expect(remote.locator('[data-project="kiri/image-tools"] strong')).toHaveText('image-tools');
  await page.screenshot({path:`${output}/${theme}-${scenario}-community.png`,fullPage:true,animations:'disabled'});
  await remote.getByRole('searchbox').fill('kiri/colmap-sfm');
  await expect(remote.locator('.hl-community-card')).toHaveCount(1);
  await expect(remote.locator('.hl-community-card strong')).toHaveText('kiri/colmap-sfm');
  await remote.getByRole('button',{name:'查看 kiri/colmap-sfm 详情',exact:true}).click();
  await expect(page.getByRole('dialog').getByRole('heading',{level:2})).toHaveText('kiri/colmap-sfm');
  await page.getByRole('button',{name:'关闭包详情',exact:true}).click();
  await remote.getByRole('searchbox').fill('image-tools');
  await remote.getByRole('button',{name:'查看 image-tools 详情',exact:true}).click();
  await expect(page.getByRole('dialog').getByRole('heading',{level:2})).toHaveText('image-tools');
  await page.getByRole('button',{name:'关闭包详情',exact:true}).click();
  await remote.getByRole('searchbox').fill('community/colmap-sfm');
  await remote.getByRole('button',{name:'查看 community/colmap-sfm 详情',exact:true}).click();
  if(scenario==='unique'){
  await page.getByRole('dialog').getByRole('button',{name:'安装',exact:true}).click();
  await expect.poll(()=>requests.some(r=>r.path==='/api/packs/install'&&JSON.parse(r.body).spec==='community/colmap-sfm@0.4.2')).toBe(true);
  await expect(page.getByRole('dialog')).toContainText('安装成功');
  } else await expect(page.getByRole('dialog').getByRole('button',{name:'已安装',exact:true})).toBeDisabled();
  await page.getByRole('button',{name:'关闭包详情',exact:true}).click();
  await page.getByRole('tab',{name:'本地安装',exact:true}).click();
  await tree.getByRole('button',{name:'colmap',exact:true}).click();
  await content.getByRole('button',{name:label,exact:true}).click();
  await page.setViewportSize({width:390,height:844});await check('package','narrow');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:`${output}/${theme}-${scenario}-narrow.png`,fullPage:true,animations:'disabled'});
  if(theme==='light'&&scenario==='unique') {
   await sidebar.getByRole('button',{name:'总览',exact:true}).click();
   await content.getByRole('searchbox').fill('no-such-package');await expect(content).toContainText('没有符合搜索条件的包');
   let release;hold=new Promise(resolve=>{release=resolve;});
   await page.reload({waitUntil:'domcontentloaded'});await expect(content).toContainText('正在读取本地包');release();hold=null;await ready();
   empty=true;await page.reload();await expect(content).toContainText('还没有发现包');
   empty=false;fail=true;await page.reload();await expect(content).toContainText('当前包目录未能读取');
   fail=false;await content.getByRole('button',{name:'重试读取'}).click();await ready();
   capabilityFail=true;await page.reload();await expect(content.getByRole('button',{name:'重试环境检查'})).toBeVisible();
   capabilityFail=false;await content.getByRole('button',{name:'重试环境检查'}).click();await ready();
  }
  expect(errors).toEqual([]);await page.close();
 }
 await writeFile(`${output}/evidence.json`,JSON.stringify(evidence,null,2));
 console.log(`PASS: ${evidence.length} page/search/selection/URL checks; local and community names, identity, loading, empty and retry checks; screenshots: ${output}`);
} finally {await browser.close();}
