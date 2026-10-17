import { afterEach, expect, it, vi } from 'vitest';
import { newId } from './newId';
afterEach(() => vi.unstubAllGlobals());
it('uses native UUID in a secure context', () => {
  const randomUUID = vi.fn(() => 'native-uuid');
  vi.stubGlobal('crypto', { randomUUID });
  expect(newId()).toBe('native-uuid');
  expect(randomUUID).toHaveBeenCalledOnce();
});
it('uses random bytes and sets RFC UUID v4 version and variant over HTTP', () => {
  const getRandomValues = vi.fn((bytes: Uint8Array) => { bytes.fill(255); return bytes; });
  vi.stubGlobal('crypto', { getRandomValues });
  expect(newId()).toBe('ffffffff-ffff-4fff-bfff-ffffffffffff');
  expect(getRandomValues).toHaveBeenCalledOnce();
});
