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
      if (u.pathname === '/api/workflows/path-test') body = {workflow_id:'path-test',name:'Path picker',created_ts:1,updated_ts:1,graph:{nodes:[{id:'source',algorithm_name:'single-video-source',algorithm_version:'0.1.0',params:{video_path:'/media/cl'},assigned_node_id:'device',position:{x:100,y:100}}],edges:[]}};
      if (u.pathname === '/api/nodes/metrics/history') body = {sample_interval_s:5,nodes:{}};
      if (u.pathname === '/api/artifacts/summary') body = {total_bytes:0,exclusive_bytes:0,artifact_count:0};
      if (u.pathname === '/api/nodes/device/paths') body = {path:'/media',parent:'/',next_offset:null,entries:[{name:'classic-stg.mp4',path:'/media/classic-stg.mp4',kind:'file',symlink:false,size:2048,modified_ts:1},{name:'renders',path:'/media/renders',kind:'directory',symlink:false,size:null,modified_ts:1}].filter(e=>e.name.startsWith(u.searchParams.get('prefix')||''))};
      await route.fulfill({json:body});
    });
    await page.goto('/w/path-test');
    await page.locator('.react-flow__node').click();
    const input = page.getByRole('combobox',{name:'video_path',exact:true});
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
