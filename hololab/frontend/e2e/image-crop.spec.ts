import {test,expect} from '@playwright/test';
async function setup(page:any,query='',broken=false){
 await page.route('**/api/handles/fixture-image/summary',r=>r.fulfill({json:{storage:'file',proxy_url:'/crop-test.png',fields:{}}}));
 await page.route('**/crop-test.png',async r=>{
  if(broken)return r.fulfill({status:404});
  const png=await page.evaluate(()=>{const c=document.createElement('canvas');c.width=320;c.height=240;return c.toDataURL().split(',')[1];});
  await r.fulfill({contentType:'image/png',body:Buffer.from(png,'base64')});
 });
 await page.goto(`/e2e/fixtures/image-crop.html${query}`);
}
test('read-only renders negative padding and disables all writes',async({page})=>{
 await setup(page,'?readonly');const editor=page.getByRole('region',{name:'Image crop editor'});
 await expect(editor.locator('canvas')).toBeVisible();
 await expect(editor.getByRole('status')).toContainText('310 × 225');
 for(const b of await editor.locator('button,input').all())await expect(b).toBeDisabled();
 const r=await editor.getByLabel('Crop rectangle',{exact:true}).boundingBox();
 await page.mouse.move(r!.x+30,r!.y+30);await page.mouse.down();await page.mouse.move(r!.x+60,r!.y+50);await page.mouse.up();
 await expect(page.locator('output')).toHaveText('0');
});
test('missing input keeps numeric editing available',async({page})=>{
 await setup(page,'?missing');await expect(page.locator('.hl-image-crop-editor [role="status"]')).toContainText('请连接 image');
 await page.getByLabel('Crop pad_left').fill('-30');await expect(page.locator('output')).toHaveText('1');
 await expect(page.locator('.hl-image-crop-stage')).toHaveCount(0);
});
test('unknown image dimensions fall back to numbers',async({page})=>{
 await setup(page,'',true);await expect(page.locator('.hl-image-crop-editor [role="status"]')).toContainText('无法读取输入图像');
 await page.getByLabel('Crop pad_top').fill('12.5');await expect(page.locator('output')).toHaveText('1');
});
test('cancelled pointer gesture does not write parameters',async({page})=>{
 await setup(page);await expect(page.locator('canvas')).toBeVisible();
 const r=await page.getByLabel('Crop rectangle',{exact:true}).boundingBox();
 await page.mouse.move(r!.x+30,r!.y+30);await page.mouse.down();await page.mouse.move(r!.x+50,r!.y+50);
 await page.keyboard.press('Escape');await page.mouse.up();
 await expect(page.locator('output')).toHaveText('0');await expect(page.getByLabel('Crop pad_left')).toHaveValue('-10');
});

// Pillow supplies real orientation-bearing files, as accepted by crop.py.
for(const format of ['JPEG','PNG','WEBP']) test(`${format} EXIF orientation does not rotate stored pixels`,async({page})=>{
 const {execFileSync}=await import('node:child_process');
 const bytes=execFileSync('python',['-c',`from PIL import Image
import sys
im=Image.new('RGB',(320,240),'red');exif=im.getexif();exif[274]=6
im.save(sys.stdout.buffer,format='${format}',exif=exif)`]);
 await page.route('**/api/handles/fixture-image/summary',r=>r.fulfill({json:{storage:'file',proxy_url:'/oriented-image',fields:{}}}));
 await page.route('**/oriented-image',r=>r.fulfill({body:bytes}));
 await page.goto('/e2e/fixtures/image-crop.html');
 await expect(page.locator('.hl-image-crop-editor [role="status"]')).toContainText('320 × 240 → 310 × 225');
 await expect(page.locator('canvas').first()).toHaveAttribute('width','320');
});
