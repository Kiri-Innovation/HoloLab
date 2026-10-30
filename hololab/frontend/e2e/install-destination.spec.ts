import {test, expect} from '@playwright/test';

test('community dialog reports selected node installation directory', async ({page}) => {
  await page.routeWebSocket('**', () => {});
  await page.route('**/api/**', route => {
    const url = new URL(route.request().url());
    const release = {name:'community/demo',version:'1.0.0',code_sha256:'a'.repeat(64),published_at:'2026-01-01T00:00:00Z'};
    let json: unknown = [];
    if (url.pathname === '/api/nodes') json = [
      {node_id:'one',node_name:'First',packs:[],gpu:{count:0},install_destination:'/installed/community'},
      {node_id:'two',node_name:'Second',packs:[],gpu:{count:0},install_destination:'/other/downloads'},
    ];
    if (url.pathname === '/api/packs/registry') json = url.searchParams.has('name')
      ? {releases:{demo:release}} : {projects:[{name:release.name}]};
    return route.fulfill({json});
  });
  await page.goto('/packs');
  await page.getByRole('tab',{name:'社区',exact:true}).click();
  await page.getByRole('button',{name:/查看 .*demo.*详情/}).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toContainText('将安装到：/installed/community');
  await page.getByLabel('安装到哪台机器').selectOption('two');
  await expect(dialog).toContainText('将安装到：/other/downloads');
});
