import {test,expect} from '@playwright/test';
import {mkdirSync} from 'node:fs';
test('install directory editor saves and clears with node default placeholder',async({page})=>{
 let config:any={file_server_host:'127.0.0.1',file_server_port:8830,node_name:'Test compute node',workspace_root:'/artifacts',legacy_workspace_roots:[],pack_dirs:['/development'],install_dir:null,default_install_dir:'/node-data/packs',install_destination:'/node-data/packs'};
 await page.route('**/api/nodes/test/config',async route=>{
  if(route.request().method()==='PATCH') {Object.assign(config,route.request().postDataJSON().patch);config.install_destination=config.install_dir || config.default_install_dir;}
  await route.fulfill({json:{node_id:'test',config}});
 });
 await page.route('**/settings-test',route=>route.fulfill({contentType:'text/html',body:`<html><body style="margin:0;background:var(--surface-1);color:var(--text-body)"><div id="root"></div><script type="module">import RefreshRuntime from '/@react-refresh';RefreshRuntime.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;window.__vite_plugin_react_preamble_installed__=true;</script><script type="module" src="/e2e/fixtures/install-settings.tsx"></script></body></html>`}));
 await page.goto('/settings-test');
 await page.getByRole('button',{name:/settings/i}).click();
 const field=page.getByRole('textbox',{name:'Install dir',exact:true});
 await expect(field).toHaveAttribute('placeholder','/node-data/packs');
 await field.fill('/downloads');await page.getByRole('button',{name:'Apply',exact:true}).click();
 await expect.poll(()=>config.install_dir).toBe('/downloads');
 await field.fill('');await page.getByRole('button',{name:'Apply',exact:true}).click();
 await expect.poll(()=>config.install_dir).toBeNull();
 mkdirSync('/tmp/install-settings',{recursive:true});
 for(const theme of ['light','dark']){await page.evaluate(t=>{document.documentElement.dataset.theme=t;document.documentElement.style.colorScheme=t;},theme);await field.scrollIntoViewIfNeeded();await page.screenshot({path:`/tmp/install-settings/${theme}.png`});}
 await page.setViewportSize({width:390,height:844});await field.scrollIntoViewIfNeeded();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await page.screenshot({path:'/tmp/install-settings/mobile.png'});
});
