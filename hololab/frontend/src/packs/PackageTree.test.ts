import {describe, expect, it} from 'vitest';
import {collapseRedundantCategory} from './PackageTree';

describe('PackageTree category rendering', () => {
  it('folds only a package category suffix that repeats its own package name', () => {
    expect(collapseRedundantCategory({name: 'kiri/video-array-assemble', version: '0.1.0', category: ['basic', 'input', 'video-array-assemble']})).toMatchObject({category: ['basic', 'input']});
    expect(collapseRedundantCategory({name: 'kiri/sfm', version: '0.4.1', category: ['reconstruction', 'colmap']})).toMatchObject({category: ['reconstruction', 'colmap']});
  });
});
