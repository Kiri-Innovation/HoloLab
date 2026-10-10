import { test, expect } from '@playwright/test';
for (const theme of ['light','dark']) test(`single status dot in ${theme}`, async ({page},info)=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.setViewportSize({width:1600,height:1150});
  await page.goto(`/e2e/fixtures/node-status.html?theme=${theme}`);
  const card=(id:string)=>page.locator(`.react-flow__node[data-id="${id}"]`);
  const dot=(id:string)=>card(id).locator('[data-hl-node-status]');
  const tip=page.getByRole('tooltip');
  const expected:Record<string,string>={idle:'neutral',pending:'info',done:'success',stale:'warning',failed:'error',orphaned:'error',interrupted:'error',drift:'warning',cancelled:'neutral',unknown:'neutral',snapshot:'success'};
  for(const [id,tone] of Object.entries(expected)){
    await expect(dot(id)).toHaveCount(1);await expect(dot(id)).toHaveAttribute('data-status-tone',tone);
    await expect(dot(id)).not.toHaveAttribute('title');
    await expect(card(id).locator('[data-hl-node-stale]')).toHaveCount(0);
    const box=(await card(id).boundingBox())!;
    expect(box.width).toBe(220);expect(box.height).toBeCloseTo(116.890625,2);
    const title=(await card(id).locator('[data-hl-node-title]').boundingBox())!;
    expect(title.x-box.x).toBe(29);
  }
  const hit=(await dot('idle').boundingBox())!;expect(hit.width).toBe(16);expect(hit.height).toBe(16);
  const visible=(await dot('idle').locator('.hl-node-status-dot').boundingBox())!;expect(visible.width).toBe(8);
  // Baseline measured before the replacement: same height, handle coordinates, edge path.
  const handles=await card('idle').locator('.react-flow__handle').evaluateAll(els=>els.map(el=>{const r=el.getBoundingClientRect();return [r.x,r.y]}));
  expect(handles).toEqual([[96,248.734375],[314,248.734375]]);
  await expect(page.locator('.react-flow__edge-path').first()).toHaveAttribute('d','M324,253.734375 C385,253.734375 385,253.734375 446,253.734375');
  await page.screenshot({path:info.outputPath(`colours-${theme}.png`)});
  for(const id of ['stale','failed','orphaned','interrupted','drift','cancelled','unknown','snapshot']){
    await dot(id).hover();await expect(tip).toHaveClass(/hl-tooltip--status/);
    if(id==='stale') {
      await expect(tip.locator('li')).toHaveCount(3);
      await expect(tip.locator('li').nth(1)).toHaveText('参数已改：ba_max_refinements');
    }
    if(id==='failed') {
      await expect(tip.locator('li')).toHaveCount(0);
      await expect(tip.locator('.hl-status-tooltip-reason')).toHaveText('磁盘空间不足');
    }
    const full=await dot(id).getAttribute('aria-label');
    expect(full).toContain(await tip.locator('strong').innerText());
    await expect(dot(id)).toHaveAttribute('aria-describedby',await tip.getAttribute('id') ?? '');
    await page.screenshot({path:info.outputPath(`${id}-${theme}.png`)});
    await page.mouse.move(0,0);
  }
  // Keyboard Tab reaches the information marker; Escape dismisses it.
  await dot('failed').focus();await page.keyboard.press('Tab');await page.keyboard.press('Shift+Tab');
  await expect(dot('failed')).toBeFocused();await expect(tip).toBeVisible();
  await page.keyboard.press('Escape');await expect(tip).toHaveCount(0);
  for(const zoom of [.5,1,1.5]){
    await dot('failed').evaluate(el=>(el as HTMLElement).blur());
    await page.locator('.react-flow__viewport').evaluate((el,z)=>(el as HTMLElement).style.transform=`translate(30px,100px) scale(${z})`,zoom);
    await dot('failed').hover();await expect(tip).toHaveCSS('--tooltip-scale',String(zoom));
    const b=(await tip.boundingBox())!;const n=(await card('failed').boundingBox())!;
    await page.screenshot({path:info.outputPath(`detail-${theme}-${zoom}.png`),clip:{x:Math.max(0,Math.min(b.x,n.x)-8),y:b.y-8,width:Math.max(b.x+b.width,n.x+n.width)-Math.max(0,Math.min(b.x,n.x)-8)+8,height:n.y+n.height-b.y+16}});
    await page.mouse.move(0,0);
  }
  expect(errors).toEqual([]);
});
