import { expect, test } from '@playwright/test';
import { applyOps } from '../src/canvas/workflowPatch';
import type { CatalogPack, WorkflowGraph } from '../src/wire';

test('checks capture_dir only on Run and keeps capability evidence separate', async ({ page }) => {
  const id = 'capture-readiness';
  const pack = {
    name: 'rig-capture-source', version: '0.1.1', manifest_hash: 'rig-011',
    node_ids: [], inputs: {}, outputs: {capture: {tags: ['rig_capture']}},
    params: {capture_dir: {type: 'path', default: '', optional: false, pinned: true}},
  } as unknown as CatalogPack;
  const graph: WorkflowGraph = {
    nodes: ['capture', 'environment', 'unavailable', 'unknown'].map((nodeId, index) => ({
      id: nodeId, algorithm_name: nodeId === 'unavailable' ? 'missing-rig-pack' : pack.name,
      algorithm_version: pack.version, position: {x: (index % 2) * 385, y: Math.floor(index / 2) * 250},
      params: {capture_dir: ''}, assigned_node_id: null,
    })),
    edges: [],
  };
  let document = {id, workflow_id: id, name: 'Capture readiness', graph, created_ts: 1, updated_ts: 1, rev: 0};
  const calls = {analyze: 0, capabilities: 0, prepare: 0, recipes: 0, dispatch: 0};
  await page.routeWebSocket('**', ws => {
    ws.onMessage(message => {
      const {kind, payload} = JSON.parse(String(message));
      if (kind !== 'workflow_edit') return;
      document = {...document, ...applyOps(document, payload.ops), rev: document.rev + 1};
      ws.send(JSON.stringify({kind: 'workflow_changed', payload: {...document, request_id: payload.request_id}}));
      ws.send(JSON.stringify({kind: 'workflow_changed', payload: {id, rev: document.rev, saved: true}}));
    });
  });
  await page.route('**/api/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    let json: unknown = [];
    if (path === '/api/pack-catalog') json = [pack];
    else if (path === '/api/nodes/metrics/history') json = {nodes: {}, sample_interval_s: 3};
    else if (path === `/api/workflows/${id}`) json = document;
    else if (path.endsWith('/device-bindings')) json = {workflow_id: id, revision: 0, bindings: {}};
    else if (path.endsWith('/canvas-state')) json = {canvas_jobs: [], staleness: {}};
    else if (path.endsWith('/analyze')) {
      calls.analyze++;
      json = {revision: 'draft', graph: request.postDataJSON().graph, canvas_jobs: [], staleness: {}, diff: [], environment_params: {}};
    } else if (path === '/api/capabilities/check') {
      calls.capabilities++;
      const {graph} = request.postDataJSON();
      expect(graph.nodes.every((node: object) => !('params' in node))).toBe(true);
      json = {nodes: {
        capture: {status: 'satisfied', issues: []},
        environment: {status: 'missing', issues: [{item: 'package:ffmpeg', required: 'installed', actual: '未安装', remedy: '安装 ffmpeg'}]},
        unknown: {status: 'unknown', issues: [{item: 'capability_check', actual: '连接超时'}]},
      }};
    } else if (path.endsWith('/prepare')) {
      calls.prepare++;
      const target = request.postDataJSON().node_id;
      expect(document.graph.nodes.find(node => node.id === 'capture')?.params.capture_dir).toBe('');
      const detail = '节点 capture: Parameter capture_dir: enter a nonempty path string';
      if (target) {
        expect(target).toBe('capture');
        await route.fulfill({status: 422, json: {detail}});
      } else {
        await route.fulfill({json: {run_blockers: [detail]}});
      }
      return;
    } else if (path === '/api/recipes/check') calls.recipes++;
    else if (path.includes('/dispatch/') || path.endsWith('/run')) calls.dispatch++;
    await route.fulfill({json});
  });
  await page.setViewportSize({width: 1400, height: 1000});
  await page.goto(`/w/${id}`);
  const card = (nodeId: string) => page.locator(`.react-flow__node[data-id="${nodeId}"]`);
  const status = (nodeId: string) => card(nodeId).locator('[data-hl-node-status]');
  const run = card('capture').getByRole('button', {name: '运行', exact: true});
  const attention = '.hl-node-status-environment-missing';
  await expect(status('environment').locator(attention)).toHaveText('!');
  await expect(status('unavailable')).toHaveAttribute('data-status-tone', 'error');
  await expect(status('unknown').locator(attention)).toHaveCount(0);
  await expect(status('capture').locator(attention)).toHaveCount(0);
  await expect(run).toBeEnabled();
  await status('capture').hover();
  await expect(page.getByRole('tooltip')).toContainText('尚未运行');
  await expect(page.getByRole('tooltip')).not.toContainText('capture_dir');
  await run.hover();
  const capabilityCalls = calls.capabilities;
  const analyzed = calls.analyze;
  const pathInput = card('capture').getByRole('combobox', {name: 'capture_dir'});
  await pathInput.fill('/other');
  await expect.poll(() => calls.analyze).toBeGreaterThan(analyzed);
  await pathInput.fill('');
  await expect.poll(() => document.graph.nodes.find(node => node.id === 'capture')?.params.capture_dir).toBe('');
  await run.hover();
  await page.waitForTimeout(500);
  expect(calls).toMatchObject({capabilities: capabilityCalls, prepare: 0, recipes: 0, dispatch: 0});
  await run.click();
  await expect(card('capture')).toContainText('Parameter capture_dir: enter a nonempty path string');
  await expect(status('capture')).toHaveAttribute('data-status-tone', 'neutral');
  await expect(status('capture').locator(attention)).toHaveCount(0);
  expect(calls).toMatchObject({capabilities: capabilityCalls, prepare: 1, recipes: 0, dispatch: 0});

  await card('capture').click({button: 'right'});
  await page.getByRole('menuitem', {name: '运行', exact: true}).click();
  await expect(page.getByRole('alert').filter({hasText: 'Parameter capture_dir'})).toBeVisible();
  await page.getByRole('button', {name: '运行流程', exact: true}).click();
  await expect(page.getByRole('dialog', {name: '无法运行工作流'})).toContainText('Parameter capture_dir');
  expect(calls).toMatchObject({capabilities: capabilityCalls, prepare: 3, recipes: 0, dispatch: 0});
});
