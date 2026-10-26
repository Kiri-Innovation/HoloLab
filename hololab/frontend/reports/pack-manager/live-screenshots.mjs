import {chromium} from 'playwright';
const browser=await chromium.launch({headless:true});
try {
 for(const theme of ['light','dark']){
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  await page.addInitScript(t=>localStorage.setItem('hololab.theme',t),theme);
  await page.goto('http://127.0.0.1:58828/packs');
  await page.getByRole('tab',{name:'社区',exact:true}).click();
  await page.getByRole('button',{name:'加载注册表',exact:true}).click();
  await page.getByRole('button',{name:'registry-smoke/hello-20261025184133',exact:true}).click({timeout:30000});
  await page.getByRole('heading',{name:'registry-smoke/hello-20261025184133',exact:true}).first().waitFor({timeout:30000});
  await page.screenshot({path:`reports/pack-manager/screenshots/live-registry-${theme}.png`,fullPage:true});
  await page.close();
 }
}finally{await browser.close();}
