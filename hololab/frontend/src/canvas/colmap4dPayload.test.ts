import {afterEach, expect, test, vi} from 'vitest';
import {colmap4dRequestUrl, loadColmap4dMetadata, loadColmap4dPayload} from './colmap4dPayload';
const base = '/proxy/node/w/workflow/j/job/colmap4d/sparse/0';
afterEach(() => vi.unstubAllGlobals());
test('inserts format adapter under the same node and authoritative handle path', () => {
  expect(colmap4dRequestUrl(base, 'points')).toBe('/proxy/node/_format/colmap4d/w/workflow/j/job/colmap4d/sparse/0?part=points');
});
test('transports opaque library files without requesting/decoding source tables', async () => {
  const fetch = vi.fn(async (_url: string) => Response.json({format:'colmap4d',part:'cameras',result:{
    files:[{name:'images.bin',base64:btoa('opaque bytes')}], total:81,shown:0,image_names:['one.png'],
    estimated:true,camera_bounds:{center:[1,2,3],radius:4},next_offset:null,
  }}));
  vi.stubGlobal('fetch', fetch);
  const p = await loadColmap4dPayload(base, 'cameras', new AbortController().signal);
  expect(await p.files[0].blob.text()).toBe('opaque bytes');
  expect(p.cameraBounds?.center).toEqual([1,2,3]);
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(fetch.mock.calls[0][0]).toContain('?part=cameras');
});
test('metadata is obtained from the format library endpoint, without frontend stat traversal', async () => {
  vi.stubGlobal('fetch',vi.fn(async()=>Response.json({format:'colmap4d',part:'metadata',result:{images_bytes:132098827,points_bytes:3149280016}})));
  expect((await loadColmap4dMetadata(base,new AbortController().signal)).images_bytes).toBe(132098827);
});
test('reports missing node dependency/endpoint and rejects mismatched responses', async () => {
  vi.stubGlobal('fetch',vi.fn(async()=>Response.json({detail:'Install hololab[preview-colmap4d]'},{status:503})));
  await expect(loadColmap4dPayload(base,'points',new AbortController().signal)).rejects.toThrow('HTTP 503');
  vi.stubGlobal('fetch',vi.fn(async()=>Response.json({format:'other',part:'points'})));
  await expect(loadColmap4dPayload(base,'points',new AbortController().signal)).rejects.toThrow('Invalid preview response');
});
