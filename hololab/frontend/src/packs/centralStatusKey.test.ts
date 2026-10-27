import { describe, expect, it } from 'vitest';
import { centralStatusKey } from './centralStatusKey';

describe('centralStatusKey', () => {
  it('uses the same canonical spelling as local pack entries', () => {
    expect(centralStatusKey('fastgs-colmap-overlay', '0.1.0')).toBe('kiri/fastgs-colmap-overlay@0.1.0');
    expect(centralStatusKey('community/colmap-sfm', '0.4.2')).toBe('community/colmap-sfm@0.4.2');
  });
});
