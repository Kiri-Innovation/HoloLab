import { test, expect } from '@playwright/test';

// Mount the actual hook; the first fetch deliberately ignores AbortSignal so
// generation/key guards, not merely transport cancellation, protect new edits.
test('late gateway analysis cannot replace the current edit', async ({ page }) => {
  await page.route('**/__analysis_harness', route => route.fulfill({ contentType: 'text/html', body: `
    <div id="root"></div><script type="module">
    import React from '/node_modules/.vite/deps/react.js';
    import ReactDOM from '/node_modules/.vite/deps/react-dom_client.js';
    import {useExecutionAnalysis} from '/src/canvas/executionAnalysis.ts';
    let finishOld;
    window.fetch = async (url, options) => {
      const value = JSON.parse(options.body).graph.nodes[0].params.count;
      const data = {revision:String(value), graph:{nodes:[],edges:[]},staleness:{},diff:[{description:String(value)}],environment_params:{}};
      if(value===1) return new Promise(resolve => {finishOld=()=>resolve({ok:true,json:async()=>data});document.body.dataset.waiting='true';});
      return {ok:true,json:async()=>data};
    };
    function App(){
      const [value,setValue]=React.useState(1);
      const result=useExecutionAnalysis('test',{nodes:[{id:'a',params:{count:value}}],edges:[]},null);
      return React.createElement('div',{},
        React.createElement('button',{onClick:()=>setValue(2)},'edit'),
        React.createElement('button',{onClick:()=>finishOld()},'release old'),
        React.createElement('output',{},result?.data?.revision??'pending'));
    }
    ReactDOM.createRoot(document.getElementById('root')).render(React.createElement(App));
    </script>` }));
  await page.goto('/__analysis_harness');
  await expect(page.locator('body')).toHaveAttribute('data-waiting','true');
  await page.getByRole('button',{name:'edit',exact:true}).click();
  await expect(page.locator('output')).toHaveText('2');
  await page.getByRole('button',{name:'release old'}).click();
  await page.waitForTimeout(300);
  await expect(page.locator('output')).toHaveText('2');
});

for (const scenario of ['default', 'debug', 'difference'] as const) {
test(`Inspector parameter evidence: ${scenario}`, async ({ page }) => {
  const pack={name:'freeze-check',version:'1.0.0',description:'Test',inputs:{},outputs:{},params:{count:{type:'int',default:12},path:{type:'path',default:'/data'}},node_ids:['compute'],arrayable:false};
  const graph={nodes:[{id:'a',kind:'algorithm',algorithm_name:pack.name,algorithm_version:pack.version,position:{x:100,y:100},params:scenario === 'difference' ? {path:'~/data'} : {},assigned_node_id:'compute'}],edges:[]};
  await page.routeWebSocket('**',()=>{});
  await page.route('**/api/**', async route => {
    const path=new URL(route.request().url()).pathname;
    let body:any=[];
    if(path==='/api/pack-catalog') body=[pack];
    else if(path==='/api/nodes')body=[{node_id:'compute',node_name:'Compute',gpu:{count:0},packs:[pack]}];
    else if(path==='/api/nodes/metrics/history')body={nodes:{}};
    else if(path==='/api/workflows/check')body={workflow_id:'check',name:'Freeze check',graph,updated_ts:1,created_ts:1};
    else if(path.endsWith('/analyze'))body={revision:'current',graph:{...graph,nodes:[{...graph.nodes[0],params:{count:12,path:scenario === 'difference' ? '/compute/data' : '/data'}}]},staleness:{a:null},diff:[],environment_params:{a:['path']}};
    await route.fulfill({json:body});
  });
  await page.setViewportSize({width:1440,height:1000});
  const analysis = page.waitForResponse(r => r.url().endsWith('/analyze'));
  await page.goto('/w/check' + (scenario === 'debug' ? '?debug=1' : ''));
  await analysis;
  await page.locator('[data-hololab-node="algorithm"]').click();
  const inspector = page.locator('.hl-node-inspector');
  if (scenario === 'debug') {
    await page.getByText('将实际使用的参数',{exact:true}).click();
    await expect(inspector.locator('pre')).toContainText('"count": 12');
    await expect(inspector.locator('pre')).toContainText('/data');
    await expect(inspector).toContainText('尚未变量化');
  } else {
    await expect(page.getByText('将实际使用的参数',{exact:true})).toHaveCount(0);
    await expect(inspector.locator('pre')).toHaveCount(0);
  }
  if (scenario === 'difference') {
    await expect(inspector.locator('.hl-parameter-difference')).toHaveText('path：显示 ~/data / 实际用 /compute/data / 环境相关路径规范化');
    await expect(inspector.getByRole('combobox', {name:'path', exact:true})).toHaveValue('~/data');
  } else {
    await expect(inspector.locator('.hl-parameter-difference')).toHaveCount(0);
  }
  await page.screenshot({path:`/tmp/hololab-inspector-${scenario}.png`});
});
}
