// Run only against a disposable Gateway: HOLOLAB_RECIPE_TEST_ROOT must match its DB parent.
import { chromium, expect } from '@playwright/test';
import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
const root = process.env.HOLOLAB_RECIPE_TEST_ROOT;
if (!root?.startsWith('/tmp/hololab-recipe-browser-')) throw Error('A disposable test Gateway is required');
const origin = process.env.HOLOLAB_URL || 'http://127.0.0.1:18764';
const document = {schema_version:1, exported_at:'2026-10-25T00:00:00Z', name:'Recipe browser test',
 graph:{nodes:[{id:'a',kind:'algorithm',algorithm_name:'missing-a',algorithm_version:'1.0.0',params:{count:12},param_bindings:{count:'iterations'}},
 {id:'b',kind:'algorithm',algorithm_name:'missing-b',algorithm_version:'1.0.0',params:{}}],
 edges:[{id:'ab',source:'a',sourceHandle:'out',target:'b',targetHandle:'in'}],variables:{iterations:{type:'int',default:12,description:'shared'},dataset:{type:'path',default:'',description:'local'}},pinned_params:{}},dependencies:[],diagnostics:[]};
const browser=await chromium.launch();
const page=await browser.newPage({viewport:{width:1800,height:1100}});
const errors=[]; page.on('pageerror', e=>errors.push(e.message));
try {
 await page.goto(origin);
 await page.getByLabel('从配方文件新建工作流').setInputFiles({name:'recipe.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(document))});
 await page.waitForURL('**/w/**');
 const wid=new URL(page.url()).pathname.split('/').at(-1);
 await expect(page.locator('.react-flow__node')).toHaveCount(2);
 await expect(page.getByText('包未发现 (未安装/未发现)',{exact:false}).first()).toBeVisible();
 await page.reload(); // Cold open must also work with an empty external catalog.
 await expect(page.locator('.react-flow__node')).toHaveCount(2);
 await expect(page.getByPlaceholder('workflow name')).toHaveValue('Recipe browser test');
 await page.getByPlaceholder('workflow name').fill('Saved from UI');
 const save=page.waitForResponse(r=>r.url().endsWith('/api/workflows')&&r.request().method()==='POST');
 await page.getByRole('button',{name:'保存配方文件',exact:true}).click();
 assert.equal((await save).status(),200);
 const path=`${root}/workflows/${wid}/recipe.json`;
 let disk=JSON.parse(await readFile(path,'utf8'));
 assert.equal(disk.name,'Saved from UI'); assert.equal(disk.graph.nodes.length,2);
 assert.equal(disk.graph.edges.length,1); assert.equal(disk.graph.variables.iterations.default,12);
 assert.ok(!JSON.stringify(disk).includes('assigned_node_id'));
 disk.name='External edit'; disk.graph.nodes[0].params.count=34;
 await writeFile(path,JSON.stringify(disk));
 await page.getByPlaceholder('workflow name').fill('Concurrent UI edit');
 const conflict=page.waitForResponse(r=>r.url().endsWith('/api/workflows')&&r.request().method()==='POST');
 await page.getByRole('button',{name:'保存配方文件',exact:true}).click();
 assert.equal((await conflict).status(),409);
 await expect(page.getByRole('dialog')).toBeVisible();
 await expect(page.getByRole('dialog')).toContainText('parameter_differences');
 await page.getByRole('button',{name:'暂不刷新'}).click();
 await page.getByRole('button',{name:'从文件重载'}).click();
 await expect(page.getByPlaceholder('workflow name')).toHaveValue('External edit');
 const detail=await (await page.request.get(`${origin}/api/workflows/${wid}`)).json();
 assert.equal(detail.graph.nodes[0].params.count,34);
 assert.deepEqual(detail.graph.variable_values,{});
 assert.ok(detail.graph.nodes.every(n=>n.assigned_node_id===null));
 const download=page.waitForEvent('download');
 await page.getByRole('button',{name:'导出配方'}).click();
 const downloaded=await download;
 assert.equal(downloaded.suggestedFilename(),'recipe.json');
 assert.deepEqual(errors,[]);
 console.log('PASS: UI import, offline cold open, two missing nodes + edge preserved, declarations/unassigned bindings, explicit save, external edit conflict, reload, export; no page errors');
} finally {await browser.close();}
