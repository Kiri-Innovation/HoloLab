import {test,expect} from '@playwright/test';
import fs from 'node:fs';
test.use({video:'on',viewport:{width:1700,height:900}});
const frame = `<!doctype html><html><body style="margin:0;background:#20242c;color:white;height:100vh;font:16px sans-serif"><div style="padding:20px">Interactive viewer fixture</div><input aria-label="Viewer input"><p id="count">0</p><script>window.wheels=0;addEventListener('wheel',e=>{e.preventDefault();document.querySelector('#count').textContent=++window.wheels},{passive:false});</script></body></html>`;
test('custom pack and LaGSplat frames share COLMAP focus gate',async({page})=>{
 page.on('pageerror',error=>console.log(error.message)); page.on('console',message=>{if(message.type()==='error')console.log(message.text());});
 await page.route('**/colmaputil/index.html?*',route=>route.fulfill({contentType:'text/html',body:frame.replace('</script>',"setTimeout(()=>parent.postMessage({type:'colmap-ready'},location.origin),100);</script>")}));
 await page.route('**/fixture-colmap/*',route=>route.fulfill({contentType:'text/plain',body:'1 0 0 0 255 255 255 0\n'}));
 await page.route('**/pack-assets/**',route=>route.fulfill({contentType:'text/html',body:frame}));
 await page.route('**/lagsplat-viewer.html',route=>route.fulfill({contentType:'text/html',body:frame}));
 await page.route('**/fixture-square*',route=>route.fulfill({contentType:'text/html',body:frame}));
 await page.route('**/focus-test',route=>route.fulfill({contentType:'text/html',body:`<!doctype html><html><head></head><body style="margin:0"><div id="root"></div><script type="module">
 import RefreshRuntime from '/@react-refresh'; RefreshRuntime.injectIntoGlobalHook(window); window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;window.__vite_plugin_react_preamble_installed__=true;
 </script><script type="module" src="/e2e/fixtures/viewer-focus.tsx"> </script></body></html>`}));
 await page.goto('/focus-test');await expect(page.locator('iframe')).toHaveCount(4);
 const viewport=page.locator('.react-flow__viewport');const transform=()=>viewport.evaluate(el=>getComputedStyle(el).transform);
 fs.mkdirSync('/tmp/viewer-focus',{recursive:true});
 for(const theme of ['light','dark']){
  await page.evaluate(t=>document.documentElement.dataset.theme=t,theme);
  for(const id of ['custom','lagsplat','square','colmap']){
   const node=page.locator(`.react-flow__node[data-id="${id}"]`),gate=node.locator('[data-viewer-interaction]'),iframe=node.locator('iframe');
   await expect(gate).toHaveAttribute('data-viewer-interaction','inactive');
   await gate.hover();const before=await transform();await page.mouse.wheel(0,70);await expect.poll(transform).not.toBe(before);
   await gate.click();await expect(gate).toHaveAttribute('data-viewer-interaction','active');
   await expect(iframe).toHaveCSS('border-top-color',await gate.evaluate(el=>{const probe=document.createElement('span');probe.style.color='var(--accent)';el.append(probe);const color=getComputedStyle(probe).color;probe.remove();return color;}));
   const box=await iframe.boundingBox();await page.mouse.move(box!.x+box!.width/2,box!.y+50);
   const active=await transform();await page.mouse.wheel(0,80);await expect.poll(()=>iframe.contentFrame().locator('#count').innerText()).not.toBe('0');expect(await transform()).toBe(active);
   await page.screenshot({path:`/tmp/viewer-focus/${theme}-${id}-active.png`});
   if(id==='custom'||id==='lagsplat') {await iframe.contentFrame().getByRole('textbox').click();await page.keyboard.press('Escape');await expect(gate).toHaveAttribute('data-viewer-interaction','inactive');await gate.click();}
   await page.mouse.click(20,750);await expect(gate).toHaveAttribute('data-viewer-interaction','inactive');
   if(id==='custom'||id==='lagsplat'){
    const b=await gate.boundingBox();const start=await transform();await page.mouse.move(b!.x+30,b!.y+30);await page.mouse.down();await page.mouse.move(b!.x+70,b!.y+50,{steps:8});await page.mouse.up();await expect.poll(transform).not.toBe(start);await expect(gate).toHaveAttribute('data-viewer-interaction','inactive');
   }
  }
 }
});
