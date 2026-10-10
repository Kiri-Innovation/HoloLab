import {test,expect} from '@playwright/test';
for(const theme of ['light','dark']) test(`arrayed broadcast and dimensions ${theme}`,async({page},info)=>{
 await page.setViewportSize({width:1200,height:800});
 let summaries=0;
 await page.route('**/api/handles/result/summary',async route=>{summaries++;await route.fulfill({json:{handle_id:'result',tags:['image','rgb'],dim_labels:['frame','cam'],dim_sizes:[100,21],element_count:100,fields:{}}});});
 await page.goto(`/e2e/fixtures/port-types.html?theme=${theme}&arrayed=1`);
 const tip=page.getByRole('tooltip');
 const check=async(selector:string,rows:string[],name:string)=>{
  await page.locator(selector).hover();
  await expect(tip.locator('.hl-type-tooltip-content > *')).toHaveText(rows);
  await page.screenshot({path:info.outputPath(`${theme}-${name}.png`)});
 };
 await expect(page.locator('.hl-arrayed-frame')).toHaveCount(1);
 await check('[data-id="n1"] .target[data-handleid="cams"]',['cams','colmap-cams','colmap-cams(pose:21 intr:1)'],'broadcast');
 await check('[data-id="n1"] .target[data-handleid="images"]',['images','arrayed<cam,frame> of any','image[cam:21][frame:100], rgb[cam:21][frame:100]'],'array-input');
 await check('[data-id="n1"] .source[data-handleid="images"]',['images','arrayed<cam,frame> of any','image[cam:21][frame:100], rgb[cam:21][frame:100]'],'array-output');
 await check('.hl-edge-chip[data-tooltip-type="images"]',['images','arrayed<cam,frame> of any','image[cam:21][frame:100], rgb[cam:21][frame:100]'],'array-edge');
 await check('[data-id="n0"] .source[data-handleid="cams"]',['cams','colmap-cams','colmap-cams(pose:21 intr:1)'],'ordinary-scalar');
 expect(summaries).toBe(1);
});
