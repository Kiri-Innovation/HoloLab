// Read-only inspection of the deployed workflow through the production gateway.
import { chromium } from '@playwright/test';
import fs from 'node:fs';
const origin = process.argv[2] || 'http://127.0.0.1:8828';
const directory = process.argv[3] || '../../reports/colmap4d-library-20261106/deployed';
fs.mkdirSync(directory, {recursive:true});
const handle = await (await fetch(origin + '/api/handles/4b0e0a4a-aca4-4811-822f-e53015da1a3f')).json();
const evidence = {proxyUrl:handle.proxy_url, requests:[], failures:[], errors:[]};
const browser = await chromium.launch({headless:true,args:['--no-sandbox','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
try {
 const page = await browser.newPage({viewport:{width:1600,height:1000}});
 const pending = [];
 page.on('request', r => {if(r.url().includes('/proxy/')) evidence.requests.push({url:r.url(),method:r.method(),range:r.headers().range});});
 page.on('pageerror', e=>evidence.errors.push(e.message));
 page.on('response', r=>{if(r.url().includes('/proxy/') && r.status()>=400) pending.push(r.text().then(body=>evidence.failures.push({url:r.url(),status:r.status(),body})).catch(e=>evidence.failures.push({url:r.url(),status:r.status(),bodyError:String(e)})));});
 await page.goto(origin+'/w/9967b1f8-3646-41f5-a269-394b6d392169');
 await page.waitForTimeout(20000);
 await page.screenshot({path:directory+'/workflow.png'});
 evidence.text = await page.locator('body').innerText();
 await Promise.allSettled(pending);
 console.log(JSON.stringify({requests:evidence.requests.length,failures:evidence.failures,errors:evidence.errors},null,2));
} finally {fs.writeFileSync(directory+'/evidence.json',JSON.stringify(evidence,null,2));await browser.close();}
