import { test, expect } from '@playwright/test';
const workflowId = '11111111-1111-1111-1111-111111111111';
const longName = 'image-undistort-with-a-very-long-algorithm-name-for-camera-calibration';
for (const theme of ['light', 'dark']) {
  test(`node title tooltip in ${theme}`, async ({ page }, info) => {
    const pageErrors: string[] = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    let name = longName;
    let device = 'kiri4090';
    let version = '0.5.0-release-candidate-with-a-long-version';
    await page.setViewportSize({width:1200,height:900});
    await page.addInitScript(t => localStorage.setItem('hololab.theme', t), theme);
    await page.routeWebSocket('**/*', () => {});
    await page.route('**/api/**', async route => {
      const path = new URL(route.request().url()).pathname;
      let body: unknown = [];
      if(path === '/api/nodes') body=[{node_id:'n',node_name:device,gpu:{total:0,gpus:[]},packs:[],connected_ts:1700000000,workspace_root:'/ws',legacy_workspace_roots:[],pack_dirs:[],advertised_url:null,flops_executor_id:null}];
      if(path === '/api/pack-catalog') body=[{name,version,manifest_hash:'x',node_ids:[],inputs:{},outputs:{out:{tags:['image']}},params:{},arrayable:false}];
      if(path === `/api/workflows/${workflowId}`) body={workflow_id:workflowId,name:'Tooltip test',created_ts:1700000000,updated_ts:1700000000,graph:{nodes:[{id:'test',algorithm_name:name,algorithm_version:version,position:{x:100,y:100},params:{},assigned_node_id:'n'}],edges:[]}};
      if(path === '/api/nodes/metrics/history') body={sample_interval_s:5,nodes:{}};
      if(path === '/api/artifacts/summary') body={total_bytes:0,exclusive_bytes:0,artifact_count:0};
      await route.fulfill({contentType:'application/json',body:JSON.stringify(body)});
    });
    const anchor=page.locator('[data-hl-node-title]');
    const tip=page.getByRole('tooltip');
    await page.goto(`/w/${workflowId}`);
    await expect(anchor).toHaveAttribute('tabindex','0');
    await expect(anchor).not.toHaveAttribute('title');
    const viewport=page.locator('.react-flow__viewport');
    for(const zoom of [.5,1,1.5]) {
      await page.mouse.move(0,0);
      await viewport.evaluate((el,z)=>(el as HTMLElement).style.transform=`translate(40px,160px) scale(${z})`,zoom);
      await anchor.hover();
      await expect(tip.locator('.hl-title-tooltip-name')).toHaveText(longName);
      await expect(tip.locator('.hl-title-tooltip-version')).toHaveText('@'+version);
      await expect(tip.locator('.hl-title-tooltip-device')).toContainText('设备');
      await expect(tip).toHaveCSS('--tooltip-scale',String(zoom));
      expect(await tip.evaluate(el=>el.scrollWidth<=el.clientWidth)).toBe(true);
      await page.screenshot({path:info.outputPath(`title-${theme}-${zoom}.png`)});
      const bubble = (await tip.boundingBox())!;
      const node = (await page.locator('[data-hololab-node="algorithm"]').boundingBox())!;
      const x = Math.max(0, Math.min(bubble.x, node.x) - 12);
      const y = Math.max(0, Math.min(bubble.y, node.y) - 12);
      await page.screenshot({path:info.outputPath(`detail-${theme}-${zoom}.png`), clip: {
        x, y, width: Math.max(bubble.x+bubble.width,node.x+node.width)+12-x,
        height: Math.max(bubble.y+bubble.height,node.y+node.height)+12-y,
      }});
    }
    await page.mouse.move(0,0);
    await viewport.evaluate(el=>(el as HTMLElement).style.transform='translate(-240px,-110px) scale(1)');
    await anchor.focus();
    await expect(tip).toBeVisible();
    await expect(anchor).toHaveAttribute('aria-describedby', await tip.getAttribute('id') ?? '');
    await expect(tip).toHaveAttribute('data-placement','below');
    const b=(await tip.boundingBox())!;
    expect(b.x).toBeGreaterThanOrEqual(8);expect(b.x+b.width).toBeLessThanOrEqual(1192);
    const a=(await anchor.boundingBox())!;
    const arrow=await tip.evaluate(el=>parseFloat((el as HTMLElement).style.getPropertyValue('--tooltip-arrow-x'))+el.clientLeft);
    expect(b.x+arrow).toBeCloseTo(a.x+a.width/2,1);
    await page.screenshot({path:info.outputPath(`title-${theme}-edge.png`)});
    await page.keyboard.press('Escape');await expect(tip).toHaveCount(0);
    await anchor.evaluate(el=>(el as HTMLElement).blur());
    // Existing simple tooltips retain plain content, with no rich-card styling.
    await viewport.evaluate(el=>(el as HTMLElement).style.transform='translate(40px,160px) scale(1)');
    await page.getByRole('button', {name:'展开预览',exact:true}).hover();
    await expect(tip).toBeVisible();await expect(tip).not.toHaveClass(/hl-tooltip--title/);
    await expect(tip).toHaveCSS('background-color', 'rgb(0, 0, 0)');
    // Short titles still expose metadata, without changing node drag behavior.
    name='a';version='1';device='pc';await page.reload();
    await expect(anchor).toHaveAttribute('tabindex','0');
    await anchor.hover();await expect(tip).toBeVisible();
    await expect(tip.locator('.hl-title-tooltip-name')).toHaveText('a');
    await expect(anchor).toHaveCSS('cursor','default');
    const card=page.locator('[data-hololab-node="algorithm"]');
    await expect(tip).toHaveCSS('background-color',await card.evaluate(el=>getComputedStyle(el).backgroundColor));
    await page.screenshot({path:info.outputPath(`short-${theme}.png`)});
    await page.mouse.move(0,0);await anchor.focus();await expect(tip).toBeVisible();
    await page.keyboard.press('Escape');await expect(tip).toHaveCount(0);
    const before=(await card.boundingBox())!;
    const titleBox=(await anchor.boundingBox())!;
    await page.mouse.move(titleBox.x+titleBox.width/2,titleBox.y+titleBox.height/2);
    await page.mouse.down();await page.mouse.move(titleBox.x+titleBox.width/2+40,titleBox.y+titleBox.height/2+30,{steps:5});
    await expect(anchor).toHaveCSS('cursor','grabbing');
    await page.mouse.up();await expect(anchor).toHaveCSS('cursor','default');
    const after=(await card.boundingBox())!;
    // React Flow consumes the initial movement when crossing its drag threshold.
    expect(after.x-before.x).toBeGreaterThan(20);expect(after.y-before.y).toBeGreaterThan(15);
    expect(pageErrors).toEqual([]);
  });
}
