import {test,expect} from '@playwright/test';
for (const theme of ['light','dark']) test(`snapshot arrayed geometry ${theme}`,async({page},info)=>{
 await page.setViewportSize({width:1200,height:800});
 await page.route('**/api/**',route=>route.fulfill({json:{}}));
 await page.goto(`/e2e/fixtures/type-ir-snapshot.html?mixed=1&theme=${theme}`);
 const frame=page.locator('[data-id="n0"] .hl-arrayed-frame');
 const ordinary=page.locator('[data-id="n1"]');
 await expect(frame).toHaveCount(1);
 await expect(page.locator('.react-flow__edge-path')).toHaveCount(1);
 await expect(ordinary.locator('.hl-arrayed-frame')).toHaveCount(0);
 const port=page.locator('[data-id="n0"] .source[data-handleid="cams"]');
 const ordinaryWidths:number[]=[];
 const geometry=()=>page.evaluate(()=>{
  const path=document.querySelector('.react-flow__edge-path') as SVGPathElement;
  if(!path) return Number.MAX_VALUE; // Wait for React Flow to finish initial measurement.
  const matrix=path.getScreenCTM()!;
  const start=path.getPointAtLength(0).matrixTransform(matrix);
  const end=path.getPointAtLength(path.getTotalLength()).matrixTransform(matrix);
  const a=document.querySelector('[data-id="n0"] .source')!.getBoundingClientRect();
  const b=document.querySelector('[data-id="n1"] .target')!.getBoundingClientRect();
  return Math.max(Math.abs(start.x-a.right),Math.abs(start.y-(a.top+a.height/2)),Math.abs(end.x-b.left),Math.abs(end.y-(b.top+b.height/2)));
 });
 for(const zoom of [0.5,1,1.5]){
  await page.getByRole('button',{name:`Zoom ${zoom}`,exact:true}).click();
  await expect.poll(geometry).toBeLessThan(1);
  ordinaryWidths.push((await ordinary.boundingBox())!.width/zoom);
  await page.mouse.move(1100,700);
  await page.screenshot({path:info.outputPath(`${theme}-frame-${zoom}.png`)});
  await port.hover();await expect(page.locator('.hl-type-tooltip-after')).toBeVisible();
  await page.screenshot({path:info.outputPath(`${theme}-arrayed-${zoom}.png`)});
 }
 // Keep the same node IDs while switching saved modes: dimensions/handles must remeasure.
 await page.getByRole('button',{name:'Switch snapshot'}).click();
 await expect(frame).toHaveCount(0);
 await expect.poll(geometry).toBeLessThan(1);
 await port.hover();await expect(page.getByRole('tooltip')).toBeVisible();
 await expect(page.locator('.hl-type-tooltip-after')).toHaveCount(0);
 await page.getByRole('button',{name:'Switch snapshot'}).click();
 await expect(frame).toHaveCount(1);
 await expect.poll(geometry).toBeLessThan(1);
 await page.locator('.hl-edge-chip').click();
 await expect(page.locator('.hl-edge-chip')).toHaveAttribute('data-selected','');
 expect(Math.max(...ordinaryWidths)-Math.min(...ordinaryWidths)).toBeLessThan(1);
});
