import {describe, it, expect} from 'vitest';
import {ApiError, isWorkflowConflict, isWorkflowPreconditionRequired} from './api';
describe('file-save conflict transport', () => {
  it.each([true, false])('recognizes FastAPI and already-unwrapped conflicts (%s)', wrapped => {
    const detail = {code:'workflow_conflict', current:{updated_ts:2}, file_changes:{nodes:['a']}};
    const error = new ApiError(409, wrapped ? {detail} : detail);
    expect(isWorkflowConflict(error)).toBe(true);
    expect(error.detail).toBe(detail);
  });
  it('recognizes wrapped revision preconditions without accepting unrelated errors', () => {
    const detail = {code:'workflow_precondition_required', current:{updated_ts:2}};
    const error = new ApiError(428, {detail});
    expect(isWorkflowPreconditionRequired(error)).toBe(true);
    expect(error.detail).toBe(detail);
    expect(isWorkflowConflict(new ApiError(409,{detail:'other conflict'}))).toBe(false);
  });
});
