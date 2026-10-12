import { describe, expect, it } from 'vitest';
import { parseRuntimeObservation } from './runtimeObservation';
import { runtimeFromLegacy, elementRuntime } from './legacyPortType';
import { renderPortType } from './renderPortType';

const payload = {
  schemaVersion: 1, handleId: 'h',
  tags: { evidence: { status: 'complete', value: ['image'], source: 'registered-handle-metadata' } },
  axes: { evidence: { status: 'sampled', source: 'handle-shape-probe', sample: 'frame_0000', value: [
    { label: 'frame', role: 'collection', size: { evidence: { status: 'complete', value: 100, source: 'directory-tree-scan' } } },
    { label: 'cam', role: 'content', size: { evidence: { status: 'sampled', value: 21, source: 'tag-content-probe', sample: 'frame_0000' } } },
  ] } }, internalCounts: [],
};
describe('versioned runtime evidence', () => {
  it('preserves sampled vs complete through element projection and terminal rendering', () => {
    const runtimeShape = parseRuntimeObservation(payload)!;
    const runtime = runtimeFromLegacy({ tags: ['image'], arrayed: true, dimLabels: ['frame','cam'], runtimeShape }, 'h');
    expect(runtime.axes.evidence.status).toBe('sampled');
    const element = elementRuntime(runtime);
    expect(element.axes.evidence.status).toBe('sampled');
    expect(renderPortType({kind:'object',tags:['image'],contentAxes:[]}, {mode:'runtime',runtime:element})).toBe('image[cam:21]');
  });
  it.each([undefined, {}, {...payload,schemaVersion:2}, {...payload,handleId:4},
    {...payload,axes:{evidence:{status:'complete',value:[{label:'cam',role:'content',size:{evidence:{status:'complete',value:-1,source:'x'}}}],source:'x'}}},
    {...payload,internalCounts:[{key:'pose',count:{evidence:{status:'sampled',value:21,source:'x'}}}]}])('rejects malformed or unsupported payloads', value => {
    expect(parseRuntimeObservation(value)).toBeUndefined();
  });
  it('keeps legacy unknown coverage and rejects evidence from another handle', () => {
    const facts = {tags:['image'],arrayed:true,dimLabels:['frame'],dimSizes:[9]};
    expect(runtimeFromLegacy(facts).axes.evidence.status).toBe('unknown');
    const mismatched = runtimeFromLegacy({...facts,runtimeShape:parseRuntimeObservation(payload)}, 'different');
    expect(mismatched.axes.evidence.status).toBe('unknown');
  });
});
