/** Validate the additive API envelope at the boundary; never trust display text. */
import type { Observation, ObservedAxis, RuntimeShape } from './portTypeIR';

type RecordValue = Record<string, unknown>;
const record = (v: unknown): v is RecordValue => typeof v === 'object' && v !== null && !Array.isArray(v);
const string = (v: unknown): v is string => typeof v === 'string';
const nullableString = (v: unknown) => v === null || string(v);
const count = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0;
function observation<T>(value: unknown, valid: (value: unknown) => value is T): value is Observation<T> {
  if (!record(value) || !record(value.evidence)) return false;
  const e = value.evidence;
  if (e.status === 'unknown') return string(e.reason);
  return (e.status === 'complete' || e.status === 'sampled' && string(e.sample)) && string(e.source) && valid(e.value);
}
const strings = (v: unknown): v is string[] => Array.isArray(v) && v.every(string);
const axes = (v: unknown): v is ObservedAxis[] => Array.isArray(v) && v.every(a => record(a)
  && nullableString(a.label) && ['collection', 'content', 'unknown'].includes(String(a.role)) && observation(a.size, count));

export function parseRuntimeObservation(value: unknown): RuntimeShape | undefined {
  if (!record(value) || value.schemaVersion !== 1 || !nullableString(value.handleId)
    || !observation(value.tags, strings) || !observation(value.axes, axes)
    || value.legacyCountKind !== undefined && !string(value.legacyCountKind)
    || !Array.isArray(value.internalCounts) || !value.internalCounts.every(c => record(c)
      && string(c.key) && observation(c.count, count))) return undefined;
  return { handleId: value.handleId as string | null, tags: value.tags, axes: value.axes,
    internalCounts: value.internalCounts, ...(string(value.legacyCountKind) ? { legacyCountKind: value.legacyCountKind } : {}) };
}
