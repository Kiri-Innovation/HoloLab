import {test,expect} from '@playwright/test';
import {mkdir,writeFile} from 'node:fs/promises';
const out='/tmp/pack-manager-resize';
test.use({video:'on',viewport:{width:1440,height:1000}});
for(const theme of ['light','dark'])test(`sidebar resize, persistence, bounds and long list (${theme})`,async({page})=>{
 await mkdir(out,{recursive:true});
 await page.addInitScript(theme=>localStorage.setItem('hololab.theme',theme),theme);
 const packs=Array.from({length:500},(_,i)=>({name:`kiri/algorithm-${String(i).padStart(3,'0')}`,version:'1.0.0',category:['research'],node_ids:['n'],params:{},inputs:{},outputs:{},description:'Algorithm development pipeline',manifest_hash:'test'}));
 await page.route('**/api/**',route=>{
  const path=new URL(route.request().url()).pathname;
  const data=path==='/api/pack-catalog'?packs:path==='/api/nodes'?[{node_id:'n',node_name:'Test compute',packs,gpu:{count:0}}]:[];
  return route.fulfill({json:data});
 });
 await page.goto('/packs?view=local&section=overview');
 const sidebar=page.locator('#pack-panel-local .hl-pack-sidebar');
 const splitter=page.getByRole('separator',{name:'调整包列表宽度'});
 await expect(sidebar.locator('.hl-pack-leaf')).toHaveCount(500);
 const width=async()=>Math.round((await sidebar.boundingBox())!.width);
 expect(await width()).toBe(300);
 const start=await splitter.boundingBox();
 await page.mouse.move(start!.x+3,start!.y+100);await page.mouse.down();
 await page.evaluate(()=>{
  const node=document.querySelector('#pack-panel-local .hl-pack-sidebar')!;
  (window as any).audit={resizes:0,mutations:0};
  const observer=new ResizeObserver(()=>{(window as any).audit.resizes++;});observer.observe(node);
  new MutationObserver(records=>{(window as any).audit.mutations+=records.length;}).observe(node,{subtree:true,childList:true,attributes:true,characterData:true});
 });
 await page.waitForTimeout(100);await page.evaluate(()=>{(window as any).audit.resizes=0;});
 await page.mouse.move(start!.x+143,start!.y+100,{steps:40});
 await page.screenshot({path:`${out}/${theme}-drag.png`});
 expect(await width()).toBe(300);
 const audit=await page.evaluate(()=>(window as any).audit);
 expect(audit).toEqual({resizes:0,mutations:0});
 await page.mouse.up();expect(await width()).toBe(440);
 await page.screenshot({path:`${out}/${theme}-released.png`});
 await page.reload();await expect(sidebar.locator('.hl-pack-leaf')).toHaveCount(500);expect(await width()).toBe(440);
 await page.screenshot({path:`${out}/${theme}-reload.png`});
 await splitter.dblclick();expect(await width()).toBe(300);
 await page.screenshot({path:`${out}/${theme}-reset.png`});
 for(const [x,expected] of [[0,200],[1400,480]]){
  const b=await splitter.boundingBox();await page.mouse.move(b!.x+3,b!.y+100);await page.mouse.down();await page.mouse.move(x,b!.y+100,{steps:15});await page.mouse.up();expect(await width()).toBe(expected);
 }
 await page.setViewportSize({width:800,height:900});expect(await width()).toBe(474);
 expect((await page.locator('#pack-panel-local .hl-pack-detail').boundingBox())!.width).toBeGreaterThanOrEqual(320);
 await page.setViewportSize({width:375,height:800});await expect(splitter).toBeHidden();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBe(375);
 await page.screenshot({path:`${out}/${theme}-narrow.png`});
 await page.setViewportSize({width:1440,height:1000});await splitter.focus();await page.keyboard.press('Home');expect(await width()).toBe(200);
 await page.keyboard.press('ArrowRight');expect(await width()).toBe(210);
 await splitter.dblclick();
 await page.getByRole('tab',{name:'社区',exact:true}).click();
 await expect(splitter).toBeVisible();await splitter.focus();await page.keyboard.press('ArrowRight');
 await page.getByRole('tab',{name:'本地安装',exact:true}).click();expect(await width()).toBe(310);
 await writeFile(`${out}/${theme}-audit.json`,JSON.stringify({rows:500,dragPointerSteps:40,duringDrag:audit,released:440,reload:440,reset:300,min:200,max:480,narrow:375},null,2));
});
