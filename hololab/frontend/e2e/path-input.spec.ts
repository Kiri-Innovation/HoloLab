import { test, expect } from '@playwright/test';
for (const theme of ['light', 'dark']) {
  test(`path picker in ${theme}`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.addInitScript(t => localStorage.setItem('hololab.theme', t), theme);
    await page.routeWebSocket('**/*', () => {});
    await page.route('**/api/**', async route => {
      const u = new URL(route.request().url()); let body: unknown = [];
      if (u.pathname === '/api/pack-catalog') body = [{ name:'single-video-source',version:'0.1.0',manifest_hash:'test',node_ids:['device'],description:'Video source',category:[],inputs:{},outputs:{},params:{video_path:{type:'path',path_kind:'file',must_exist:true}} }];
      if (u.pathname === '/api/nodes') body = [{node_id:'device',node_name:'Test node',online:true,gpu:{count:0},packs:[],legacy_workspace_roots:[],pack_dirs:[],workspace_root:'/media'}];
      if (u.pathname === '/api/workflows/path-test') body = {workflow_id:'path-test',name:'Path picker',created_ts:1,updated_ts:1,graph:{nodes:[{id:'source',algorithm_name:'single-video-source',algorithm_version:'0.1.0',params:{},assigned_node_id:'device',position:{x:100,y:100}}],edges:[]}};
      if (u.pathname === '/api/nodes/metrics/history') body = {sample_interval_s:5,nodes:{}};
      if (u.pathname === '/api/artifacts/summary') body = {total_bytes:0,exclusive_bytes:0,artifact_count:0};
      if (u.pathname === '/api/nodes/device/paths') body = {path:'/media',parent:'/',next_offset:null,entries:[{name:'classic-stg.mp4',path:'/media/classic-stg.mp4',kind:'file',symlink:false,size:2048,modified_ts:1},{name:'renders',path:'/media/renders',kind:'directory',symlink:false,size:null,modified_ts:1}].filter(e=>e.name.startsWith(u.searchParams.get('prefix')||''))};
      await route.fulfill({json:body});
    });
    await page.goto('/w/path-test');
    await page.locator('.react-flow__node').click();
    const input = page.getByRole('combobox',{name:'video_path',exact:true});
    await expect(input).toHaveValue('/media');
    await page.getByRole('button',{name:'Browse paths for video_path'}).click();
    await expect(page.getByLabel('Location',{exact:true})).toHaveValue('/media/');
    await page.getByRole('button',{name:'Close path browser'}).click();
    await input.fill('/media/cl');
    await expect(page.getByRole('option',{name:'classic-stg.mp4',exact:true})).toBeVisible();
    await input.press('Tab');
    await expect(input).toHaveValue('/media/classic-stg.mp4');
    await page.getByRole('button',{name:'Browse paths for video_path'}).click();
    const dialog=page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    const entries=page.getByRole('listbox',{name:'Directory entries'});
    await expect(entries.getByRole('option')).toHaveCount(2);
    await page.screenshot({path:testInfo.outputPath(`browser-${theme}.png`)});
    await entries.focus(); await entries.press('ArrowDown'); await entries.press('Enter');
    await expect(dialog).not.toBeVisible(); await expect(input).toHaveValue('/media/classic-stg.mp4');
    await page.setViewportSize({width:390,height:844});
    await page.getByRole('tab',{name:'Inspect',exact:true}).click();
    await page.getByRole('button',{name:'Browse paths for video_path'}).click();
    const bounds=await dialog.boundingBox();
    expect(bounds!.x).toBeGreaterThanOrEqual(0); expect(bounds!.x+bounds!.width).toBeLessThanOrEqual(390);
    expect(await dialog.evaluate(e=>e.scrollWidth<=e.clientWidth)).toBe(true);
    await page.screenshot({path:testInfo.outputPath(`browser-narrow-${theme}.png`)});
    await page.keyboard.press('Escape'); await expect(dialog).not.toBeVisible();
  });
}

for (const theme of ['light','dark']) {
  test(`live path browsing defaults, debounce and stale responses in ${theme}`, async ({page}, testInfo) => {
    await page.setViewportSize({width:1440,height:1000});
    await page.addInitScript(t=>localStorage.setItem('hololab.theme',t),theme);
    await page.routeWebSocket('**/*',()=>{});
    const requests: Array<{path:string,prefix:string}> = [];
    let releaseSlow: (()=>void) | undefined;
    const slowGate = new Promise<void>(resolve=>{releaseSlow=resolve;});
    await page.route('**/api/**',async route=>{
      const u=new URL(route.request().url()); let body:unknown=[];
      if(u.pathname==='/api/pack-catalog') body=[{name:'single-video-source',version:'0.1.0',manifest_hash:'test',node_ids:['device'],inputs:{},outputs:{},params:{video_path:{type:'path',path_kind:'file'}}}];
      if(u.pathname==='/api/nodes') body=[{node_id:'device',node_name:'Test node',gpu:{count:0},packs:[],legacy_workspace_roots:[],pack_dirs:[],workspace_root:null}];
      if(u.pathname==='/api/workflows/path-test') body={workflow_id:'path-test',name:'Path picker',created_ts:1,updated_ts:1,graph:{nodes:[{id:'source',algorithm_name:'single-video-source',algorithm_version:'0.1.0',params:{},assigned_node_id:'device',position:{x:100,y:100}}],edges:[]}};
      if(u.pathname==='/api/nodes/metrics/history') body={sample_interval_s:5,nodes:{}};
      if(u.pathname==='/api/nodes/device/paths') {
        const path=u.searchParams.get('path')!; const prefix=u.searchParams.get('prefix')!;
        requests.push({path,prefix});
        if(path==='/slow/') await slowGate;
        const name=path==='/slow/'?'stale-result':path==='/'?'cloud':path==='/cloud/'?'cloud-ssd1':'clip.mp4';
        body={path:path.replace(/\/$/,'')||'/',parent:'/',next_offset:null,entries:[{name,path:(path==='/'?'/':path)+name,kind:name==='clip.mp4'?'file':'directory',symlink:false,size:1,modified_ts:1}].filter(e=>path==='/slow/'||e.name.startsWith(prefix))};
      }
      await route.fulfill({json:body}).catch(()=>{});
    });
    await page.goto('/w/path-test');
    await page.locator('.react-flow__node').click();
    const input=page.getByRole('combobox',{name:'video_path',exact:true});
    await expect(input).toHaveValue('/');
    await page.getByRole('button',{name:'Browse paths for video_path'}).click();
    const dialog=page.getByRole('dialog'); const location=dialog.getByLabel('Location',{exact:true});
    const entries=dialog.getByRole('listbox',{name:'Directory entries'});
    await expect(location).toHaveValue('/');
    await expect(entries.getByRole('option')).toHaveCount(1);
    await expect(dialog.getByRole('button',{name:'Go',exact:true})).toHaveCount(0);
    await page.screenshot({path:testInfo.outputPath(`root-${theme}.png`)});
    const start=requests.length;
    await location.fill('/cloud/c'); await location.fill('/cloud/cl');
    await page.waitForTimeout(100); expect(requests.length).toBe(start);
    await expect(entries.getByRole('option')).toContainText('cloud-ssd1');
    expect(requests.slice(start)).toEqual([{path:'/cloud/',prefix:'cl'}]);
    await expect(location).toHaveValue('/cloud/cl');
    await page.screenshot({path:testInfo.outputPath(`live-${theme}.png`)});
    await location.fill('/slow/a');
    await expect.poll(()=>requests.at(-1)?.path).toBe('/slow/');
    await location.fill('/cloud/cl');
    await expect(entries.getByRole('option')).toContainText('cloud-ssd1');
    releaseSlow!(); await page.waitForTimeout(150);
    await expect(entries).not.toContainText('stale-result');
    await expect(location).toHaveValue('/cloud/cl');
    await location.press('ArrowDown'); await location.press('Tab');
    await expect(location).toHaveValue('/cloud/cloud-ssd1/');
    await expect(entries.getByRole('option')).toContainText('clip.mp4');
    await location.press('ArrowDown'); await location.press('ArrowUp'); await location.press('Enter');
    await expect(dialog).not.toBeVisible();
    await expect(input).toHaveValue('/cloud/cloud-ssd1/clip.mp4');
  });
}
