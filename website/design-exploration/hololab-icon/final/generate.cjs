// Hand-authored vector master. Raster exports use the same geometry at every size.
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require('../../../node_modules/playwright');
const root=path.resolve(__dirname,'../../../..');
const assets=path.join(__dirname,'assets');
const svg=`<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64" role="img" aria-label="HoloLab flask">
  <rect width="64" height="64" rx="16" fill="#30E3E3"/>
  <path fill="#FFFFFF" fill-rule="evenodd" d="M25 14H39V18H38V26.5Q38 27.3 38.5 28.1L50 47.2Q52.4 52 47 52H17Q11.6 52 14 47.2L25.5 28.1Q26 27.3 26 26.5V18H25Z M24 38Q32 40 40 38L42.4 42Q32 44 21.6 42Z"/>
</svg>
`;
(async()=>{
 fs.writeFileSync(path.join(assets,'hololab-icon.svg'),svg);
 fs.writeFileSync(path.join(assets,'favicon.svg'),svg);
 for(const theme of ['light','dark'])fs.writeFileSync(path.join(assets,`favicon-${theme}.svg`),svg);
 const browser=await chromium.launch({args:['--no-sandbox']});
 const page=await browser.newPage({deviceScaleFactor:1});
 for(const size of [16,32,48,192,512]){
  await page.setContent(`<style>body{margin:0}svg{display:block;width:${size}px;height:${size}px}</style>${svg}`);
  const name=size<100?`favicon-${size}.png`:`app-icon-${size}.png`;
  await page.locator('svg').screenshot({path:path.join(assets,name),omitBackground:true});
  if(size<100)for(const theme of ['light','dark'])fs.copyFileSync(path.join(assets,name),path.join(assets,`favicon-${theme}-${size}.png`));
 }
 await browser.close();
 for(const site of ['hololab/frontend','website']){
  const dest=path.join(root,site,'public');
  for(const name of fs.readdirSync(assets))fs.copyFileSync(path.join(assets,name),path.join(dest,name));
 }
})();
