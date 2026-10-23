import { expect, it } from 'vitest';
import { parameterEvidence } from './parameterEvidence';

it('shows only recorded values and marks missing history without catalog defaults', () => {
  expect(parameterEvidence(undefined)).toBe('历史未记录');
  expect(parameterEvidence({ count: 12 }, ['count', 'newDefault'])).toBe('{\n  "count": 12\n}\n历史未记录：newDefault');
  expect(parameterEvidence({ value: null, array: [], text: '' }, ['value', 'array', 'text'])).not.toContain('历史未记录');
  expect(parameterEvidence({})).toBe('{}');
  expect(parameterEvidence({ count: 10 })).not.toEqual(parameterEvidence({ count: 12 }));
});
