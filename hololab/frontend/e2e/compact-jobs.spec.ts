import { expect, test } from '@playwright/test';

test('Jobs keeps versions inline and numeric columns aligned without wrapping', async ({ page }) => {
  await page.routeWebSocket('**', () => {});
  const jobs = [
    ['fastgs-freeze-reference', 51, 51],
    ['stg-train', 10000, 10000],
    ['a-deliberately-long-algorithm-name-that-must-yield-space-to-its-version', 3000, 3000],
  ].map(([name, current, total], i) => ({job_id:`job-${i}`,workflow_id:'compact-jobs',algorithm_name:name,algorithm_version:'0.1.0',state:'done',created_ts:1,updated_ts:13,graph_node_id:null,progress:{current,total}}));
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    const body = path === '/api/jobs' ? jobs
      : path === '/api/workflows/compact-jobs' ? {workflow_id:'compact-jobs',name:'Jobs layout check',graph:{nodes:[],edges:[]},created_ts:1,updated_ts:1}
      : path === '/api/nodes/metrics/history' ? {sample_interval_s:3,nodes:{}} : [];
    await route.fulfill({contentType:'application/json',body:JSON.stringify(body)});
  });
  await page.setViewportSize({width:1440,height:1000});
  await page.goto('/w/compact-jobs');
  await page.getByRole('tab',{name:'Jobs',exact:true}).click();
  for (const width of [1440,390]) {
    await page.setViewportSize({width,height:width===390?844:1000});
    if(width===390) await page.getByRole('tab',{name:'Jobs',exact:true}).click();
    const rows=page.locator('.hl-job-row:visible');
    await expect(rows).toHaveCount(3);
    const geometry=await rows.evaluateAll(rows=>rows.map(row=>{
      const name=row.children[1].querySelector('strong')!;
      const version=row.children[1].querySelector('span')!;
      const progress=row.children[2];const elapsed=row.children[3];
      return {height:row.getBoundingClientRect().height,nameTop:name.getBoundingClientRect().top,versionTop:version.getBoundingClientRect().top,versionFull:version.scrollWidth<=version.clientWidth,progressRight:progress.getBoundingClientRect().right,elapsedRight:elapsed.getBoundingClientRect().right,nowrap:getComputedStyle(progress).whiteSpace,elapsedNowrap:getComputedStyle(elapsed).whiteSpace,progressFull:progress.scrollWidth<=progress.clientWidth};
    }));
    for(const row of geometry){
      expect(row.height).toBeLessThanOrEqual(36);
      expect(Math.abs(row.nameTop-row.versionTop)).toBeLessThan(4);
      expect(row.versionFull).toBe(true);
      expect(row.nowrap).toBe('nowrap'); expect(row.elapsedNowrap).toBe('nowrap');
      expect(row.progressFull).toBe(true);
      expect(row.progressRight).toBe(geometry[0].progressRight);
      expect(row.elapsedRight).toBe(geometry[0].elapsedRight);
    }
    console.log({width,geometry});
    for(const theme of ['light','dark']){
      await page.evaluate(t=>document.documentElement.dataset.theme=t,theme);
      await page.waitForTimeout(300);
      await page.screenshot({path:`test-results/compact-jobs-${width}-${theme}.png`});
    }
  }
});
