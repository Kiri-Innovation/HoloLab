import {test,expect} from '@playwright/test';
for(const theme of ['light','dark']) test(`strict image contracts ${theme}`,async({page},info)=>{
 await page.setViewportSize({width:1200,height:1000});
 await page.route('**/fixture-image.png',r=>r.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAAYElEQVR4nO3PAQkAMQDEsHuY6Ml/GWWQUAP9tnu2lzt7moGagZqBmoGagZqBmoGagZqBmoGagZqBmoGagZqBmoGagZqBmoGagZqBmoGagZqBmoGagZqBmoGagZqBmoHaD6B/A24XY8UeAAAAAElFTkSuQmCC','base64')}));
 await page.goto(`/e2e/fixtures/strict-image.html?theme=${theme}`);
 const tip=page.getByRole('tooltip');
 const shapes=()=>page.locator('.react-flow__node,.react-flow__handle').evaluateAll(es=>es.map(e=>{const r=e.getBoundingClientRect();return [r.x,r.y,r.width,r.height]}));
 const before=await shapes();
 const anchors=['[data-id="und"] .target[data-handleid="images"]','[data-id="und"] .source[data-handleid="images"]','.hl-edge-chip[data-tooltip-type="images"]','[data-id="und"] span[data-tooltip-type="images"]'];
 for(const [i,selector] of anchors.entries()){
  await page.locator(selector).first().hover();
  await expect(tip.locator('.hl-type-tooltip-before > *')).toHaveText(['images','arrayed[cam]<image>','image[cam:21]']);
  await expect(tip.locator('.hl-type-tooltip-after > *')).toHaveText(['after arrayed','arrayed<arrayed[cam]<image>>','image[cam:21][frame:100]']);
  await page.screenshot({path:info.outputPath(`${theme}-strict-${i}.png`)});
 }
 expect(await shapes()).toEqual(before);
 await page.locator('[data-id="und"] .target[data-handleid="cams"]').hover();
 await expect(tip.locator('.hl-type-tooltip-after > *')).toHaveText(['arrayed (auto-popped)','colmap-cams','colmap-cams(pose:21 intr:1)']);
 await page.screenshot({path:info.outputPath(`${theme}-broadcast.png`)});
 await page.locator('[data-id="s"] .source[data-handleid="images"]').hover();
 await expect(tip.locator('.hl-type-tooltip-content > *')).toHaveText(['images','arrayed[frame]<arrayed[cam]<image>>','image[cam:21][frame:100]']);
 const file=page.locator('#single img');await expect(file).toBeVisible();expect(await file.evaluate((e:HTMLImageElement)=>e.naturalWidth)).toBe(64);
 for(const z of [.5,1,1.5]){
  await page.mouse.move(0,0);
  await page.locator('.react-flow__viewport').evaluate((e,z)=>(e as HTMLElement).style.transform=`translate(-100px,0px) scale(${z})`,z);
  await page.locator(anchors[0]).hover();await expect(tip).toHaveCSS('--tooltip-scale',String(z));
 }
});
