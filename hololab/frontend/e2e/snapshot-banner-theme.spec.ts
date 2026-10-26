import {test, expect} from '@playwright/test';

for (const theme of ['light', 'dark']) {
  test(`snapshot banner follows ${theme} theme`, async ({page}, testInfo) => {
    await page.setViewportSize({width:1200,height:700});
    await page.route('**/api/**', route=>route.fulfill({json:{}}));
    await page.goto(`/e2e/fixtures/type-ir-snapshot.html?banner=1&legacy=1&theme=${theme}`);
    const banner=page.locator('.hl-snapshot-banner');
    const colours=await banner.evaluate(el=>{
      const s=getComputedStyle(el);
      return {background:s.backgroundColor,foreground:s.color,border:s.borderBottomColor};
    });
    expect(colours.background).toBe(theme==='dark'?'rgb(37, 37, 38)':'rgb(23, 23, 23)');
    if(theme==='dark') {
      expect(colours.foreground).toBe('rgb(204, 204, 204)');
      expect(colours.border).toBe('rgb(60, 60, 60)');
    }
    await expect(page.getByRole('button',{name:'返回草稿',exact:true})).toHaveCSS('color',colours.foreground);
    await expect(page.getByRole('button',{name:'恢复快照',exact:true})).toHaveCSS('background-color',theme==='dark'?'rgb(0, 0, 0)':'rgb(23, 23, 23)');
    await page.screenshot({path:testInfo.outputPath(`snapshot-${theme}.png`)});
    await page.getByRole('button',{name:'返回草稿',exact:true}).click();
    await expect(page.locator('body')).toHaveAttribute('data-back','true');
  });
}
