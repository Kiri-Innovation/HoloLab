import {expect,test} from '@playwright/test';

test('union input tooltip shows every accepted shape',async({page},info)=>{
  await page.setViewportSize({width:900,height:560});
  await page.goto('/e2e/fixtures/union-port.html');
  const card=page.locator('[data-id="stg"]');
  const input=card.locator('.react-flow__handle.target[data-handleid="colmap_4d"]');
  await expect(card).toBeVisible();
  await input.hover();
  const tooltip=page.getByRole('tooltip');
  await expect(tooltip.locator('.hl-type-tooltip-content > :nth-child(2)'))
    .toHaveText('colmap4d | arrayed[frame]<colmap-folder>');
  await page.screenshot({path:info.outputPath('stg-init-0.3.1-union-tooltip.png')});
});
