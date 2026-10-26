import {test,expect} from '@playwright/test';

test('node-card edges render exact and ragged dimensions without question marks',async({page})=>{
 await page.setViewportSize({width:1400,height:800});
 await page.goto('/e2e/fixtures/ragged-dimensions.html');
 await expect(page.locator('.hl-edge-chip')).toHaveCount(3);
 const labels=await page.locator('.hl-edge-chip').allTextContents();
 expect(labels).toContain('image[cam:6–7][group:159]');
 expect(labels).toContain('colmap-cams[group:159]');
 expect(labels).toContain('image[1110]');
 expect(labels.join(' ')).not.toContain('?');
 await expect(page.locator('.react-flow__node[data-id="merge4d_g0"]')).toContainText('model');
});
