import {test, expect, type Page} from '@playwright/test';
import {mkdirSync} from 'node:fs';
import {resolve} from 'node:path';

// This is deliberately a live acceptance test: pack-manager evidence must
// describe the packs actually available on kiri4090, never a routed fixture.
const liveBaseURL = 'http://117.50.186.141:8828';
const screenshotDirectory = resolve(process.cwd(), 'e2e/screenshots/pack-manager');
const detailPack = 'colmap-sfm';

async function open(page: Page, path: string, theme: 'light' | 'dark') {
  await page.addInitScript(value => localStorage.setItem('hololab.theme', value), theme);
  await page.setViewportSize({width: 1440, height: 1000});
  await page.goto(`${liveBaseURL}${path}`, {waitUntil: 'domcontentloaded'});
  // The product title is a top-bar label, not a heading.
  await expect(page.getByText('· Pack Manager', {exact: true})).toBeVisible();
}

async function waitForLocalPacks(page: Page) {
  await expect(page.getByRole('tab', {name: '本地安装', exact: true})).toBeVisible();
  await expect(page.getByRole('tab', {name: '社区', exact: true})).toBeVisible();
  await expect(page.getByRole('tab', {name: '按分类', exact: true})).toBeVisible();
  await expect(page.getByRole('tab', {name: '按作用域', exact: true})).toBeVisible();
  await expect.poll(() => page.evaluate(async () => {
    const response = await fetch('/api/pack-catalog');
    return response.ok ? (await response.json()).length : -1;
  }), {timeout: 30_000}).toBe(59);
  await expect(page.getByText('正在读取本地包…', {exact: true})).toHaveCount(0);
}

async function openCommunityDetail(page: Page) {
  const tree = page.getByRole('navigation', {name: '按分类的本地包目录'});
  await expect(tree.getByRole('button', {name: 'reconstruction', exact: true})).toBeVisible();
  await tree.getByRole('button', {name: '展开 colmap', exact: true}).click();
  await tree.getByRole('button', {name: new RegExp(`^${detailPack}\\s`)}).click();
}

for (const theme of ['light', 'dark'] as const) {
  test(`live Pack Manager local list and tree evidence (${theme})`, async ({page}) => {
    mkdirSync(screenshotDirectory, {recursive: true});
    await open(page, '/packs?view=local&section=overview', theme);
    await waitForLocalPacks(page);

    // The root category is expanded; `fastgs` remains a single folder below it.
    const tree = page.getByRole('navigation', {name: '按分类的本地包目录'});
    await expect(tree.getByRole('button', {name: 'gaussian-recon', exact: true})).toBeVisible();
    await tree.getByRole('button', {name: '展开 fastgs', exact: true}).click();
    await expect(tree.getByRole('button', {name: /^fastgs-pano-train\s/})).toBeVisible();
    await page.screenshot({path: `${screenshotDirectory}/local-list-${theme}.png`, fullPage: true, animations: 'disabled'});
  });

  test(`live Pack Manager package detail evidence (${theme})`, async ({page}) => {
    mkdirSync(screenshotDirectory, {recursive: true});
    await open(page, '/packs?view=local&section=overview', theme);
    await waitForLocalPacks(page);
    await openCommunityDetail(page);
    const detail = page.getByRole('region', {name: '本地包内容'});
    await expect(detail.getByRole('heading', {name: new RegExp(`community/${detailPack}@0\\.4\\.2$`)})).toBeVisible();
    const environment = detail.locator('.hl-pack-environment');
    await expect(environment).toHaveAttribute('open', '');
    await expect(environment).toContainText('要求');
    await expect(environment).toContainText('逻辑环境');
    await expect(environment).toContainText('实际路径');
    await expect(environment).toContainText('检查结果');
    await expect(detail.locator('.hl-pack-status-chip')).toHaveCount(3, {timeout: 30_000});
    await expect(detail.locator('.hl-pack-status-summary')).not.toContainText('检查中', {timeout: 30_000});
    await expect(detail.getByRole('region', {name: '包状态'})).toContainText('社区版');
    await expect(detail.getByRole('region', {name: '包状态'})).toContainText('已安装');
    await expect(detail.getByRole('region', {name: '包状态'})).toContainText('环境就绪');
    await expect(detail.getByRole('navigation', {name: '包路径'})).toHaveText(new RegExp(`${detailPack}$`));
    await page.screenshot({path: `${screenshotDirectory}/package-detail-${theme}.png`, fullPage: true, animations: 'disabled'});
  });

  test(`live Pack Manager community evidence (${theme})`, async ({page}) => {
    mkdirSync(screenshotDirectory, {recursive: true});
    await open(page, '/packs?view=community&section=home', theme);
    await expect(page.getByRole('tab', {name: '社区', exact: true})).toBeVisible();
    const community = page.getByRole('region', {name: '社区包'});
    await expect(community.getByRole('heading', {name: '首页', exact: true})).toBeVisible();
    await expect(community.getByLabel('搜索社区包')).toBeVisible();
    await expect(community.locator('.hl-community-grid')).toBeVisible();
    // The registry is live and may legitimately be empty, but the completed
    // view must not be left in its loading state.
    await expect(community.getByRole('status')).toBeHidden({timeout: 30_000});
    await page.screenshot({path: `${screenshotDirectory}/community-${theme}.png`, fullPage: true, animations: 'disabled'});
  });
}
