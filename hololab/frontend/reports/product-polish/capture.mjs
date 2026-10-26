import {chromium} from '@playwright/test';
import {mkdir,writeFile} from 'node:fs/promises';
const phase=process.argv[2]||'before';
await mkdir(new URL(`./${phase}/`,import.meta.url),{recursive:true});
const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
const page=await browser.newPage();
const audit=[];
page.on('pageerror',e=>console.error(e));
for(const theme of ['light','dark'])for(const width of [1280,390]){
 await page.setViewportSize({width,height:1000});
 await page.goto(`${process.env.POLISH_ORIGIN || 'http://127.0.0.1:5173'}/reports/product-polish/index.html`);
 await page.evaluate(t=>document.documentElement.dataset.theme=t,theme);
 await page.locator('[data-shot="variables"] > details').evaluate(el=>el.open=true);
 await page.locator('[data-shot="environment"] > details').evaluate(el=>el.open=true);
 await page.locator('[data-shot="identity"] > details').evaluate(el=>el.open=true);
 audit.push({theme,width,...await page.evaluate(()=>({pageWidth:document.documentElement.scrollWidth,viewport:innerWidth,panels:Array.from(document.querySelectorAll('[data-shot]')).map(el=>({name:el.getAttribute('data-shot'),width:el.clientWidth,contentWidth:el.scrollWidth}))}))});
 await page.screenshot({animations:'disabled',path:new URL(`./${phase}/${theme}-${width}.png`,import.meta.url).pathname,fullPage:true});
 for(const section of ['recipe','snapshot','variables','environment','identity']){
  await page.locator(`[data-shot="${section}"]`).screenshot({animations:'disabled',path:new URL(`./${phase}/${section}-${theme}-${width}.png`,import.meta.url).pathname});
 }
 for(const section of ['identity','environment']){
  const panel=page.locator(`[data-shot="${section}"] > details`);
  await panel.locator('details').evaluateAll(nodes=>nodes.forEach(el=>el.open=true));
  await panel.evaluate(el=>el.scrollTop=el.scrollHeight);
  await panel.screenshot({animations:'disabled',path:new URL(`./${phase}/${section}-expanded-${theme}-${width}.png`,import.meta.url).pathname});
 }
 await page.locator('[data-shot="snapshot"] details').first().evaluate(el=>el.open=true);
 await page.locator('[data-shot="snapshot"]').screenshot({animations:'disabled',path:new URL(`./${phase}/snapshot-expanded-${theme}-${width}.png`,import.meta.url).pathname});
}
await writeFile(new URL(`./${phase}/layout-audit.json`,import.meta.url),JSON.stringify(audit,null,2));
await browser.close();
