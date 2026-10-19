import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
const origin=process.env.WEBSITE_URL ?? 'http://127.0.0.1:5250';
const output='test-results/skill-copy';await mkdir(output,{recursive:true});
const b=await chromium.launch();
try {
for(const lang of ['en','zh-CN']) {
 const context=await b.newContext({permissions:['clipboard-read','clipboard-write'],viewport:{width:1280,height:1800}});
 const p=await context.newPage();await p.goto(`${origin}${lang==='en'?'':'/zh-CN'}/docs/use/agent-oriented`);
 const blocks=p.locator('.copyable-code');assert.equal(await blocks.count(),2);
 for(const block of await blocks.all()) {
  await block.getByRole('button').click();
  assert.equal(await p.evaluate(()=>navigator.clipboard.readText()),await block.locator('code').textContent());
 }
 assert.match(await blocks.first().locator('code').textContent(),/raw.githubusercontent.com\/Kiri-Innovation\/HoloLab\/main\/skill\/SKILL.md/);
 assert.match(await blocks.first().locator('code').textContent(),/<URL>/);
 const heading=p.locator('h2').filter({hasText:lang==='en'?'Everything included in a skill':'一切包含在技能中'});
 for(const theme of ['light','dark']) {
  await p.locator('.theme-control').selectOption(theme);
  await heading.evaluate(el=>window.scrollTo(0,el.getBoundingClientRect().top+scrollY-90));
  const box=await heading.boundingBox();const bottom=await p.locator('h2').last().boundingBox();
  await p.screenshot({path:`${output}/${lang}-${theme}.png`,clip:{x:box.x,y:box.y,width:box.width,height:bottom.y-box.y}});
 }
 // Force denied Clipboard API to exercise legacy fallback and failure guidance.
 await p.evaluate(()=>{
  Object.defineProperty(navigator,'clipboard',{value:{writeText:async()=>{throw Error('denied');}},configurable:true});
  document.execCommand=command=>{window.fallbackText=document.activeElement.value;return command==='copy';};
 });
 await blocks.first().getByRole('button').click();assert.equal(await p.evaluate(()=>window.fallbackText),await blocks.first().locator('code').textContent());
 await p.evaluate(()=>{document.execCommand=()=>false;});await blocks.first().getByRole('button').click();
 await p.waitForFunction(()=>document.querySelector('[role="status"]').textContent.includes('失败')||document.querySelector('[role="status"]').textContent.includes('failed'));
 await p.setViewportSize({width:390,height:844});assert.ok(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await context.close();
}
console.log('Passed: EN/ZH both blocks copy exact text; fallback/error guidance; themes and 390px layout.');
}finally{await b.close();}
