const {chromium}=require('../../../node_modules/playwright');
const fs=require('node:fs');const path=require('node:path');const os=require('node:os');const {execFileSync}=require('node:child_process');const {pathToFileURL}=require('node:url');
(async()=>{
 const out=path.join(__dirname,'screenshots');const audit=[];
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
 const p=await browser.newPage({viewport:{width:1200,height:1000},deviceScaleFactor:1});
 await p.goto(pathToFileURL(path.join(__dirname,'index.html')).href);
 await p.screenshot({path:path.join(out,'final-preview.png'),fullPage:true});
 for(const theme of ['light','dark'])await p.locator('#'+theme).screenshot({path:path.join(out,`final-16-${theme}.png`)});
 await browser.close();
 for(const theme of ['light','dark']){
  const profile=fs.mkdtempSync(path.join(os.tmpdir(),'hololab-icon-check-'));
  const context=await chromium.launchPersistentContext(profile,{headless:false,viewport:null,colorScheme:theme,env:{...process.env,DISPLAY:':97'},args:['--no-sandbox','--window-size=1440,1000','--window-position=0,0','--force-device-scale-factor=1',...(theme==='dark'?['--force-dark-mode']:[])]});
  await context.addInitScript(theme=>{localStorage.setItem('hololab.theme',theme);localStorage.setItem('hololab.website.theme',theme);localStorage.setItem('hololab.website.locale','en');},theme);
  const page=context.pages()[0];
  for(const [name,url] of [['product','http://127.0.0.1:8828/'],['website','http://127.0.0.1:5200/']]){
   await page.goto(url);await page.locator('img[src="/hololab-icon.svg"]').waitFor();await page.waitForTimeout(1500);
   const entry=await page.evaluate(()=>({theme:document.documentElement.dataset.theme,title:document.title,icons:[...document.querySelectorAll('link[rel="icon"]')].map(i=>({href:i.href,sizes:i.sizes.value})),logoLoaded:[...document.querySelectorAll('img[src="/hololab-icon.svg"]')].every(i=>i.complete&&i.naturalWidth===64)}));
   for(const icon of entry.icons){const r=await page.request.get(icon.href);if(!r.ok())throw Error('Missing favicon '+icon.href);}
   if(entry.theme!==theme||!entry.logoLoaded)throw Error('Failed theme/logo '+name);
   audit.push({name,...entry});
   await page.screenshot({path:path.join(out,`${name}-${theme}-page.png`)});
   execFileSync('python',['-c','from PIL import ImageGrab; import sys; ImageGrab.grab(xdisplay=":97").save(sys.argv[1])',path.join(out,`${name}-${theme}-browser.png`)]);
  }
  // Read-only verification of the other product brand locations.
  for(const route of ['/artifacts','/library']){
   await page.goto('http://127.0.0.1:8828'+route);await page.locator('img[src="/hololab-icon.svg"]').waitFor();
   audit.push({name:'product'+route,theme,logo:await page.locator('img[src="/hololab-icon.svg"]').count()});
  }
  await page.setViewportSize({width:390,height:844});await page.goto('http://127.0.0.1:5200/');
  await page.locator('.header img').waitFor();
  audit.push({name:'website-mobile',theme,overflow:await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)});
  await page.screenshot({path:path.join(out,`website-${theme}-mobile.png`)});
  await context.close();fs.rmSync(profile,{recursive:true,force:true});
 }
 fs.writeFileSync(path.join(__dirname,'verification.json'),JSON.stringify(audit,null,2)+'\n');console.log(audit);
})();
