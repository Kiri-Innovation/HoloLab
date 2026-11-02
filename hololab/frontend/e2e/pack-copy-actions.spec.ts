import {test, expect} from '@playwright/test';

for (const installed of [true, false]) test(`pack copy feedback and source guide (installed=${installed})`, async ({page}) => {
  await page.addInitScript(() => {
    window.flops = {version: 1, showDocument: async () => ({success: true}), insertReference: async () => ({success: true})};
    // Exercise the HTTP fallback even on the localhost test server.
    Object.defineProperty(window, 'isSecureContext', {value: false});
    (window as any).copied = [];
    const copy = document.execCommand.bind(document);
    document.execCommand = (...args) => {
      (window as any).copied.push((document.activeElement as HTMLTextAreaElement).value);
      return copy(...args);
    };
  });
  const pack = {name: 'community/copy-check', version: '1.0.0', node_ids: installed ? ['n'] : ['other'], inputs: {}, outputs: {}, params: {}, manifest_hash: 'test', source_dir: '/data/packs/copy-check', central: {state: 'identical'}, publication: {source: 'https://example.org'}};
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    return route.fulfill({json: path === '/api/pack-catalog' ? [pack] : path === '/api/nodes' ? [{node_id: 'n', node_name: 'Test compute', packs: installed ? [pack] : [], gpu: {count: 0}}] : []});
  });
  await page.goto('/packs?view=local&section=category&pack=community%2Fcopy-check%401.0.0');
  const copyButton = page.getByRole('button', {name: installed ? '复制路径' : '复制安装命令', exact: true});
  await copyButton.click();
  await expect(page.locator('.hl-pack-copy-status')).toContainText(installed ? '本地路径已复制。' : '安装命令已复制；尚未执行安装。');
  const command = await page.locator('.hl-pack-command').innerText();
  expect(await page.evaluate(() => (window as any).copied)).toEqual([command]);
  await expect(page.locator('[data-hl-open-cocoder-guide]')).toHaveCount(0);
  if (installed) {
    const source = page.getByRole('button', {name: '查看代码', exact: true});
    await expect(source).toHaveText('查看代码');
    await source.click();
    const guide = page.locator('[data-hl-open-cocoder-guide]');
    await expect(guide).toContainText('fill the Flops executor id field');
    await expect(source).not.toHaveText('!');
    expect(await guide.evaluate(el => el.parentElement === document.body)).toBe(true);
    for (const width of [1440, 390]) {
      await page.setViewportSize({width, height: 844});
      await expect.poll(async () => {
        const b = (await guide.boundingBox())!;
        return b.x >= 8 && b.x + b.width <= width - 8 && b.y >= 8 && b.y + b.height <= 836;
      }).toBe(true);
    }
  }
  await page.evaluate(() => { document.execCommand = () => false; });
  await copyButton.click();
  await expect(page.locator('.hl-pack-copy-status')).toContainText('未能复制，请选中');
});
