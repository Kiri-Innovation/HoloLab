import {describe, expect, it} from 'vitest';
import {collapseRedundantCategory} from './packCategory';

describe('PackageTree category rendering', () => {
  it('folds only an exact package category suffix or a hyphen-delimited package suffix', () => {
    expect(collapseRedundantCategory({name: 'kiri/video-array-assemble', version: '0.1.0', category: ['basic', 'input', 'video-array-assemble']})).toMatchObject({category: ['basic', 'input']});
    expect(collapseRedundantCategory({name: 'fastgs-colmap-overlay', version: '0.1.0', category: ['gaussian-recon', 'fastgs', 'colmap-overlay']})).toMatchObject({category: ['gaussian-recon', 'fastgs']});
    expect(collapseRedundantCategory({name: 'kiri/sfm', version: '0.4.1', category: ['reconstruction', 'colmap']})).toMatchObject({category: ['reconstruction', 'colmap']});
    expect(collapseRedundantCategory({name: 'kiri/colmap-sfm', version: '0.4.1', category: ['reconstruction', 'colmap']})).toMatchObject({category: ['reconstruction', 'colmap']});
    expect(collapseRedundantCategory({name: 'kiri/uncategorized', version: '0.1.0', category: []})).toMatchObject({category: []});
  });
});
