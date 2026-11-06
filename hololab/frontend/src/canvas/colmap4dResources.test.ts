import { afterEach, expect, test, vi } from 'vitest';
import { fileSize, imageDirectorySize, imagePath, LOAD_CONFIRM_BYTES } from './colmap4dResources';
afterEach(() => vi.unstubAllGlobals());
test('range metadata uses full length, not one-byte body; threshold is decimal MB', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('x', {status: 206, headers: {'Content-Range': 'bytes 0-0/2477433608'}})));
  expect(await fileSize('/points.bin')).toBe(2477433608);
  expect(LOAD_CONFIRM_BYTES).toBe(200000000);
});
test('directory bytes include nested and unreferenced images across pages', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    const q = new URL(url, 'http://localhost').searchParams;
    const data = q.get('path') === '/images/sub' ? {entries: [{kind: 'file', size: 30}], next_offset: null}
      : q.get('offset') === '0' ? {entries: [{kind: 'directory', path: '/images/sub'}, {kind: 'file', size: 20}], next_offset: 2}
      : {entries: [{kind: 'file', size: 10}], next_offset: null};
    return Response.json(data);
  }));
  expect(await imageDirectorySize('node', '/images', new AbortController().signal)).toBe(60);
});
test('unknown length and unsafe image names do not silently load', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('x')));
  await expect(fileSize('/unknown')).rejects.toThrow('Unknown');
  expect(() => imagePath('../secret')).toThrow('Unsafe');
  expect(imagePath('cam/a #.png')).toBe('cam/a%20%23.png');
});
