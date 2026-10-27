import {describe, expect, it} from 'vitest';
import {collapseRedundantCategory} from './packCategory';

describe('PackageTree category rendering', () => {
  it('folds exact and compound redundant suffixes without removing a meaningful category', () => {
    expect(collapseRedundantCategory({name: 'fastgs-colmap-overlay', version: '0.1.0', category: ['gaussian-recon', 'fastgs', 'colmap-overlay']})).toMatchObject({category: ['gaussian-recon', 'fastgs']});
    expect(collapseRedundantCategory({name: 'community/merge-colmap', version: '0.4.0', category: ['reconstruction', 'colmap']})).toMatchObject({category: ['reconstruction', 'colmap']});
    expect(collapseRedundantCategory({name: 'community/colmap-sfm', version: '0.4.2', category: ['reconstruction', 'colmap']})).toMatchObject({category: ['reconstruction', 'colmap']});
    expect(collapseRedundantCategory({name: 'builtin/get-index', version: '0.1.0', category: ['basic', 'array', 'get-index']})).toMatchObject({category: ['basic', 'array']});
    expect(collapseRedundantCategory({name: 'kiri/uncategorized', version: '0.1.0', category: []})).toMatchObject({category: []});
  });
});
