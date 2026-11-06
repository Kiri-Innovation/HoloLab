import {expect, test} from 'vitest';
import {colmap4dUrl} from './colmap4dUrls';
const root = '/proxy/node/w/workflow/j/job/colmap4d';
test('keeps handle proxy_url and joins sparse or root component without empty/dot segments', () => {
  expect(colmap4dUrl(root, 'sparse/0')).toBe(root + '/sparse/0');
  expect(colmap4dUrl(root + '/', '/sparse//0/')).toBe(root + '/sparse/0');
  expect(colmap4dUrl(root, '.')).toBe(root);
  expect(colmap4dUrl(root + '/./', 'cameras.bin')).toBe(root + '/cameras.bin');
});
test('does not reconstruct an absolute filesystem URL or accept parent traversal', () => {
  expect(() => colmap4dUrl('/cloud/output/model')).toThrow('proxy_url');
  expect(() => colmap4dUrl(root, '../private')).toThrow('member');
});
