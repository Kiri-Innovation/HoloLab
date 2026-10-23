import { afterEach, expect, it, vi } from 'vitest';
import { dispatchNode, rerunFromNode, runWorkflow } from './api';

afterEach(() => { vi.unstubAllGlobals(); });
it.each([false, true])('dispatch and rerun use valid request IDs (native randomUUID: %s)', async native => {
  let seed = 0;
  const randomUUID = vi.fn(() => '12345678-1234-4234-8234-123456789abc');
  const getRandomValues = vi.fn((bytes: Uint8Array) => { bytes.fill(++seed); return bytes; });
  vi.stubGlobal('crypto', native ? { randomUUID, getRandomValues } : { getRandomValues });
  const fetch = vi.fn<typeof globalThis.fetch>(async url => new Response(JSON.stringify(String(url).endsWith('/prepare') ? {token:'prepared'} : {}), {status:200}));
  vi.stubGlobal('fetch', fetch);
  await dispatchNode('workflow', 'node');
  await runWorkflow('workflow');
  await rerunFromNode('snapshot', 'node');
  const requests = fetch.mock.calls.filter(([url]) => !String(url).endsWith('/prepare'));
  expect(requests).toHaveLength(3);
  for (const [, options] of requests) {
    const headers = new Headers(options?.headers);
    expect(headers.get('X-Request-ID')).toMatch(/^[\da-f]{8}-[\da-f]{4}-4[\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$/i);
  }
  expect(new Headers(requests[0][1]?.headers).get('X-Preparation-Token')).toBe('prepared');
  expect(new Headers(requests[1][1]?.headers).get('X-Preparation-Token')).toBe('prepared');
  if (native) expect(randomUUID).toHaveBeenCalledTimes(3);
  else expect(getRandomValues).toHaveBeenCalledTimes(3);
});
