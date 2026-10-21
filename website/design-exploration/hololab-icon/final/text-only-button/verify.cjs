const {chromium}=require('../../../../node_modules/playwright');
const fs=require('node:fs');const path=require('node:path');
(async()=>{
 const browser=await chromium.launch({args:['--no-sandbox']});const records=[];
 for(const theme of ['light','dark']){
  const context=await browser.newContext({viewport:{width:1440,height:900},deviceScaleFactor:1,colorScheme:theme});
  await context.addInitScript(t=>localStorage.setItem('hololab.theme',t),theme);
  const page=await context.newPage();
  const response=await page.request.get('http://127.0.0.1:8828/api/workflows');const workflows=await response.json();
  for(const [name,route] of [['workspace','/w/'+encodeURIComponent(workflows[0].workflow_id)],['artifacts','/artifacts']]){
   await page.goto('http://127.0.0.1:8828'+route);
   const button=page.getByRole('button',{name:'返回流程列表',exact:true});await button.waitFor();
   await page.evaluate(()=>document.fonts.ready);
   if((await button.innerText()).trim()!=='HoloLab'||await button.locator('img,svg,span').count())throw Error('Button is not text only');
   const geometry=await button.evaluate(el=>{const s=getComputedStyle(el);return {text:el.textContent.trim(),align:s.alignItems,justify:s.justifyContent,padding:s.padding,fontSize:s.fontSize};});
   const favicon=await page.locator('link[rel="icon"][type="image/svg+xml"]').getAttribute('href');
   if(favicon!=='/favicon.svg')throw Error('Unexpected favicon');
   const r=await page.request.get('http://127.0.0.1:8828/favicon.svg');if(!r.ok()||!(await r.text()).includes('#30E3E3'))throw Error('Missing favicon');
   await page.screenshot({path:path.join(__dirname,`${name}-${theme}.png`),clip:{x:0,y:0,width:1440,height:180}});
   records.push({name,theme,...geometry,favicon});
  }
  await context.close();
 }
 fs.writeFileSync(path.join(__dirname,'verification.json'),JSON.stringify(records,null,2)+'\n');console.log(records);await browser.close();
})();
