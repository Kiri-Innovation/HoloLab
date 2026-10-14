import { chromium } from '@playwright/test';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
const origin=process.env.WEBSITE_URL ?? 'http://127.0.0.1:5259';
const output='test-results/doc-screenshots';await mkdir(output,{recursive:true});
const manifest=JSON.parse(await readFile('public/images/docs/manifest.json'));
assert.equal(manifest.length,18);
for(const m of manifest){assert.ok(m.bytes <= (m.file.startsWith('canvas-')?400:200)*1024);assert.equal(m.captureScale,2);}
const b=await chromium.launch();const p=await b.newPage({viewport:{width:1440,height:1000},locale:'en-US'});const errors=[];p.on('pageerror',e=>errors.push(e.message));
try {
for(const lang of ['en','zh-CN'])for(const slug of ['quick-start','use/workflows','use/run-records']){
  const name=slug.split('/').at(-1);await p.goto(origin+(lang==='en'?'':'/zh-CN')+'/docs/'+slug);
  assert.equal(await p.locator('.doc-screenshot').count(),3);
  for(const theme of ['light','dark']){
    await p.locator('.theme-control').selectOption(theme);
    for(const [i,fig] of (await p.locator('.doc-screenshot').all()).entries()){
      await fig.scrollIntoViewIfNeeded();await p.waitForFunction(({i,theme})=>{const img=document.querySelectorAll('.doc-screenshot img')[i];return img.complete&&img.naturalWidth>0&&img.currentSrc.endsWith(`-${theme}.webp`);},{i,theme});
      if(lang==='en')await fig.screenshot({path:`${output}/${name}-${i+1}-${theme}.png`});
    }
    await p.evaluate(()=>scrollTo(0,0));await p.screenshot({path:`${output}/${lang}-${name}-${theme}.png`,fullPage:true});
    await p.setViewportSize({width:390,height:844});
    assert.ok(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    for(const img of await p.locator('.doc-screenshot img').all()){const box=await img.boundingBox();assert.ok(box.width<=390);}
    await p.screenshot({path:`${output}/${lang}-${name}-${theme}-mobile.png`,fullPage:true});
    await p.setViewportSize({width:1440,height:1000});
  }
}
assert.deepEqual(errors,[]);
await writeFile(`${output}/verification.json`,JSON.stringify({figures:9,assets:18,totalBytes:manifest.reduce((n,m)=>n+m.bytes,0),locales:['en','zh-CN'],themes:['light','dark'],viewportWidths:[1440,390],errors},null,2));
console.log('Passed: 9 figures / 18 assets; manual light/dark theme switching; both languages; desktop and mobile; image budgets.');
}finally{await b.close();}
