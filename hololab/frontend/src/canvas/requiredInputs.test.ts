import { expect, it } from 'vitest';
import { missingRequiredInputs } from './requiredInputs';
it('distinguishes required missing ports from optional empty ports', () => {
  const inputs = {videos: {required:true}, transforms:{required:true}, offsets:{required:true}, optional:{required:false}};
  expect(missingRequiredInputs(inputs, ['videos', 'offsets'])).toEqual(['transforms']);
  expect(missingRequiredInputs(inputs, ['videos','offsets','transforms'])).toEqual([]);
  expect(missingRequiredInputs(inputs, [null, 'unknown'])).toEqual(['videos','transforms','offsets']);
  expect(missingRequiredInputs({}, [])).toEqual([]);
});
