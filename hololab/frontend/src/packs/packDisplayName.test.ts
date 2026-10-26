import {expect, it} from 'vitest';
import {packDisplayNames} from './packDisplayName';

it('omits scope for unique names, including builtin packages', () => {
  const label = packDisplayNames(['kiri/colmap-sfm', 'community/image-tools', '@builtin/view']);
  expect(label('kiri/colmap-sfm')).toBe('colmap-sfm');
  expect(label('community/image-tools')).toBe('image-tools');
  expect(label('@builtin/view')).toBe('view');
});
it('keeps scope for every distinct identity sharing a name', () => {
  const label = packDisplayNames(['kiri/colmap-sfm', 'community/colmap-sfm', 'kiri/image-tools']);
  expect(label('kiri/colmap-sfm')).toBe('kiri/colmap-sfm');
  expect(label('community/colmap-sfm')).toBe('community/colmap-sfm');
  expect(label('kiri/image-tools')).toBe('image-tools');
});
it('does not treat versions or legacy aliases of the same identity as collisions', () => {
  const label = packDisplayNames(['colmap-sfm', 'kiri/colmap-sfm', 'kiri/colmap-sfm']);
  expect(label('colmap-sfm')).toBe('colmap-sfm');
  expect(label('kiri/colmap-sfm')).toBe('colmap-sfm');
});
it('uses the full catalog even when the rendered subset contains only one match', () => {
  const names = ['kiri/sfm', 'community/sfm', 'community/load'];
  const label = packDisplayNames(names);
  expect(names.filter(name => name.startsWith('kiri/')).map(label)).toEqual(['kiri/sfm']);
  expect(names).toEqual(['kiri/sfm', 'community/sfm', 'community/load']);
});
it('keeps local and community collision domains independent', () => {
  expect(packDisplayNames(['kiri/sfm'])('kiri/sfm')).toBe('sfm');
  expect(packDisplayNames(['kiri/sfm', 'community/sfm'])('kiri/sfm')).toBe('kiri/sfm');
});
