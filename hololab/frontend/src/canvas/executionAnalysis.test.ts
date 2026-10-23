import { expect, it } from 'vitest';
import { AnalysisGeneration } from './executionAnalysis';

it('a delayed old response cannot overwrite a new edit or a disposed request', async () => {
  const generation = new AnalysisGeneration();
  let completeOld!: () => void;
  let display = '';
  const old = generation.next();
  const pending = new Promise<void>(resolve => { completeOld = resolve; }).then(() => {
    if (generation.current(old)) display = 'old';
  });
  const next = generation.next();
  if (generation.current(next)) display = 'new';
  completeOld();
  await pending;
  expect(display).toBe('new');
  generation.next();
  expect(generation.current(next)).toBe(false);
});
