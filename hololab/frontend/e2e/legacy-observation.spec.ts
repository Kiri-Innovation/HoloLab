import {test,expect} from '@playwright/test';
for(const theme of ['light','dark']) test(`historical response without evidence remains usable ${theme}`,async({page},info)=>{
 const errors:string[]=[];
 page.on('pageerror',error=>errors.push(error.message));
 await page.setViewportSize({width:1200,height:800});
 await page.route('**/api/**',route=>{
  if(route.request().url().endsWith('/old-handle/summary')) return route.fulfill({json:{
   handle_id:'old-handle',tags:['colmap-cams'],fields:{entries:[{is_dir:true,entry_count:999}]},
   dim_labels:['frame'],dim_sizes:[999],internal_count_items:[{label:'pose',value:888}]}});
  return route.fulfill({status:404,json:{detail:'unavailable historical handle'}});
 });
 await page.goto(`/e2e/fixtures/type-ir-snapshot.html?theme=${theme}&legacy=1`);
 const edge=page.locator('.hl-edge-chip');await expect(edge).toHaveCount(1);
 const port=page.locator('[data-id="n1"] .target[data-handleid="cams"]');
 const before=await port.boundingBox();
 await edge.hover();
 const tip=page.getByRole('tooltip');await expect(tip).toBeVisible();
 await expect(tip.locator('.hl-type-tooltip-content > :nth-child(3)')).toHaveText('未知');
 await expect(edge).toContainText('colmap-cams[historical-frame:?]');
 await expect(tip).not.toContainText('999');await expect(edge).not.toContainText('888');
 await page.screenshot({path:info.outputPath(`${theme}-legacy-unknown.png`)});
 await port.hover();await expect(tip.locator('.hl-type-tooltip-content > :nth-child(3)')).toHaveText('未知');
 expect(await port.boundingBox()).toEqual(before);
 await edge.click();await expect(edge).toHaveAttribute('data-selected','');
 expect(errors).toEqual([]);
});
