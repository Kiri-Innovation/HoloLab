import {test,expect} from '@playwright/test';
for(const theme of ['light','dark']) test(`arrayed broadcast and dimensions ${theme}`,async({page},info)=>{
 await page.setViewportSize({width:1200,height:800});
 let summaries=0;
 let nested=false;
 await page.route('**/api/handles/result/summary',async route=>{summaries++;await route.fulfill({json:{handle_id:'result',tags:['image'],dim_labels:nested?['batch','frame','cam']:['frame','cam'],dim_sizes:nested?[2,100,21]:[100,21],element_count:100,fields:{}}});});
 await page.goto(`/e2e/fixtures/port-types.html?theme=${theme}&arrayed=1`);
 const tip=page.getByRole('tooltip');
 const check=async(selector:string,rows:string[],name:string)=>{
  await page.locator(selector).first().hover();
  await expect(tip).toBeVisible();
  if(name==='ordinary-scalar') await expect(tip.locator('.hl-type-tooltip-content > *')).toHaveText(rows);
  else {
    await expect(tip.locator('.hl-type-tooltip-before > *')).toHaveText(rows);
    const broadcast=name.startsWith('broadcast');
    const full=broadcast?rows[2]:rows[2]+(nested?'[batch:2]':'[frame:100]');
    await expect(tip.locator('.hl-type-tooltip-after > *')).toHaveText([broadcast?'auto arrayed':'after arrayed',broadcast?rows[1]:`arrayed<${rows[1]}>`,full]);
    if(broadcast) await expect(tip.locator('.hl-type-tooltip-explanation')).toHaveText('广播：每个分片收到同一份完整输入');
  }
  await page.screenshot({path:info.outputPath(`${theme}-${name}.png`)});
 };
 await expect(page.locator('.hl-arrayed-frame')).toHaveCount(1);
 await check('[data-id="n1"] .target[data-handleid="cams"]',['cams','colmap-cams','colmap-cams(pose:21 intr:1)'],'broadcast');
 await check('.hl-edge-chip[data-tooltip-type="cams"]',['cams','colmap-cams','colmap-cams(pose:21 intr:1)'],'broadcast-edge');
 await check('[data-id="n1"] span[data-tooltip-type="cams"]',['cams','colmap-cams','colmap-cams(pose:21 intr:1)'],'broadcast-text');
 await check('[data-id="n1"] .target[data-handleid="frames"]',['frames','arrayed<image>','image[cam:21]'],'array-input');
 await check('[data-id="n1"] .source[data-handleid="frames"]',['frames','arrayed<image>','image[cam:21]'],'array-output');
 await check('.hl-edge-chip[data-tooltip-type="frames"]',['frames','arrayed<image>','image[cam:21]'],'array-edge');
 await check('[data-id="n0"] .source[data-handleid="cams"]',['cams','colmap-cams','colmap-cams(pose:21 intr:1)'],'ordinary-scalar');
 await check('[data-id="n1"] .target[data-handleid="points"]',['points','arrayed<point-cloud>','point-cloud(pt:7519)'],'points-element');
 await check('[data-id="n1"] span[data-tooltip-type="frames"]',['frames','arrayed<image>','image[cam:21]'],'port-text');
 expect(summaries).toBe(1);
 nested=true;await page.reload();
 for(const zoom of [.5,1,1.5]) {
  await page.mouse.move(0,0);
  await page.locator('.react-flow__viewport').evaluate((el,z)=>(el as HTMLElement).style.transform=`translate(-200px,0px) scale(${z})`,zoom);
  await check('[data-id="n1"] .target[data-handleid="frames"]',['frames','arrayed<image>','image[cam:21][frame:100]'],`nested-${zoom}`);
  await expect(tip).toHaveCSS('--tooltip-scale',String(zoom));
 }
 await page.setViewportSize({width:420,height:700});
 await page.locator('.react-flow__viewport').evaluate(el=>(el as HTMLElement).style.transform='translate(-500px,-100px) scale(1)');
 await page.mouse.move(0,0);
 await check('[data-id="n1"] .target[data-handleid="frames"]',['frames','arrayed<image>','image[cam:21][frame:100]'],'narrow');
 await expect(tip).toHaveAttribute('data-stacked','true');
 const rect=(await tip.boundingBox())!;expect(rect.x).toBeGreaterThanOrEqual(8);expect(rect.x+rect.width).toBeLessThanOrEqual(412);
});
