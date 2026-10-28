import {describe, expect, it} from 'vitest';
import {packRegistryStatus} from './packRegistryStatus';

describe('packRegistryStatus', () => {
  it('treats bundled packages as trusted before their absent registry state', () => {
    expect(packRegistryStatus('builtin/video-array-source', {state: 'absent'})).toMatchObject({
      tone: 'success', conclusion: '内置包',
    });
  });

  it('keeps local-only and registry-parity labels unchanged for other packages', () => {
    expect(packRegistryStatus('kiri/local-frame-extraction', {state: 'absent'})).toMatchObject({
      tone: 'neutral', conclusion: '本地包',
    });
    expect(packRegistryStatus('community/colmap-sfm', {
      state: 'identical', source: 'https://registry.example', central_code_sha256: 'abc',
    })).toMatchObject({tone: 'success', conclusion: '社区版'});
  });
});
