import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import {
  InlineValuesPanel,
  inlineCell,
  type InlineField,
} from './CanvasInlineValues';
import type { InputPortSpec } from '../wire';
const spec = {
  tags: ['float'],
  storage: 'file',
  required: true,
} as InputPortSpec;
const field = (value: unknown, array = false): InlineField => ({
  name: 'offset',
  spec,
  value,
  array,
});
describe('canvas inline preview', () => {
  it('shows exact scalar and vector values, including zero and invalid values', () => {
    expect(inlineCell(0)).toBe('0');
    expect(inlineCell([0, 0.727, 0.25])).toBe('0 · 0.727 · 0.25');
    expect(inlineCell(Infinity)).toBe('Invalid value');
    const html = renderToStaticMarkup(
      <InlineValuesPanel fields={[field(0)]} />,
    );
    expect(html).toContain('read only');
    expect(html).toContain('>0</code>');
  });
  it('bounds array reads regardless of array size', () => {
    let reads = 0;
    const values = new Proxy(new Array(100000).fill(0.727), {
      get(target, key, receiver) {
        if (typeof key === 'string' && /^\d+$/.test(key)) reads++;
        return Reflect.get(target, key, receiver);
      },
    });
    const html = renderToStaticMarkup(
      <InlineValuesPanel fields={[field(values, true)]} />,
    );
    expect(reads).toBe(3);
    expect(html).toContain('99997 remaining');
    expect((html.match(/class="hl-inline-row"/g) ?? []).length).toBe(3);
  });
  it('has empty and compact modes without mounting array rows', () => {
    expect(renderToStaticMarkup(<InlineValuesPanel fields={[]} />)).toBe('');
    expect(
      renderToStaticMarkup(<InlineValuesPanel fields={[field([], true)]} />),
    ).toContain('Empty array');
    const html = renderToStaticMarkup(
      <InlineValuesPanel fields={[field([1, 2, 3], true)]} compact />,
    );
    expect(html).toContain('zoom in');
    expect(html).not.toContain('hl-inline-row');
  });
  it('limits simultaneous ports to four', () => {
    const fields = Array.from({ length: 100 }, (_, i) => ({
      ...field(i),
      name: `port-${i}`,
    }));
    const html = renderToStaticMarkup(<InlineValuesPanel fields={fields} />);
    expect((html.match(/class="hl-inline-field"/g) ?? []).length).toBe(4);
    expect(html).toContain('Next input fields');
  });
});
