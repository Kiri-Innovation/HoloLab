import { afterEach, expect, it, vi } from 'vitest';
import { CHUNK, sendMissing } from './api';
afterEach(() => vi.unstubAllGlobals());
it('hashes upload chunks without WebCrypto and preserves resume behavior', async () => {
  vi.stubGlobal('crypto', {});
  const fetch = vi.fn<typeof globalThis.fetch>(async () => new Response('{}'));
  vi.stubGlobal('fetch', fetch);
  const file = new Blob(['abc']) as File;
  const session = { upload_id: 'test', chunk_size: CHUNK, parts: [] } as any;
  await sendMissing(file, session, () => {}, new AbortController().signal);
  expect((fetch.mock.calls[0][1]?.headers as Record<string, string>)['X-Chunk-Sha256']).toBe('ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
  fetch.mockClear();
  await sendMissing(file, { ...session, parts: [{ part_index: 0, size_bytes: 3 }] }, () => {}, new AbortController().signal);
  expect(fetch).not.toHaveBeenCalled();
});
