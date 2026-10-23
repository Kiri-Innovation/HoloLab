import { describe, expect, it } from 'vitest';
import { cropRect, cropError, cropViewport, dragCrop, pixelPadding, type Pads } from './imageCropGeometry';
const pads: Pads = { pad_left: 31, pad_top: 17, pad_right: 53, pad_bottom: 29 };
describe('stored-pixel image crop geometry', () => {
  it('uses top-left origin and independent right/bottom distances', () => {
    expect(cropRect(320, 240, pads)).toEqual({ x: 31, y: 17, width: 236, height: 194 });
    expect(dragCrop(320, 240, pads, 'move', 10, -5)).toEqual({ pad_left: 41, pad_top: 12, pad_right: 43, pad_bottom: 34 });
  });
  it('rounds positive and negative halves exactly like the backend', () => {
    expect([0.49, 0.5, -0.5, -1.5, 1.5].map(pixelPadding)).toEqual([0, 1, -1, -2, 2]);
  });
  it.each(['n','ne','e','se','s','sw','w','nw'] as const)('resizes only the %s sides', side => {
    const p = dragCrop(320, 240, pads, side, 10, 20);
    expect(p).toEqual({pad_left: 31 + (side.includes('w') ? 10 : 0), pad_top: 17 + (side.includes('n') ? 20 : 0), pad_right: 53 - (side.includes('e') ? 10 : 0), pad_bottom: 29 - (side.includes('s') ? 20 : 0)});
  });
  it('allows expansion and includes it in the viewport', () => {
    const p = dragCrop(320, 240, pads, 'nw', -60, -40);
    expect(p.pad_left).toBe(-29); expect(p.pad_top).toBe(-23);
    const v = cropViewport(320, 240, p);
    expect(v.x).toBeLessThan(-29); expect(v.y).toBeLessThan(-23);
    expect(v.x + v.width).toBeGreaterThan(320);
  });
  it('retains an original pixel at resize and move boundaries', () => {
    for (const side of ['move','nw','se'] as const) for (const delta of [-10000, 10000]) {
      expect(cropError(320, 240, dragCrop(320, 240, pads, side, delta, delta))).toBeNull();
    }
    expect(dragCrop(320, 240, pads, 'w', 10000, 0).pad_left).toBe(266);
  });
  it('rejects empty original crops even when opposite padding expands output', () => {
    expect(cropError(320, 240, {...pads, pad_left: 320, pad_right: -400})).toMatch(/原图/);
    expect(cropError(320, 240, {...pads, pad_top: NaN})).toMatch(/有限/);
    expect(cropError(320, 240, {...pads, pad_left: -100000})).toMatch(/限制/);
    expect(cropError(320, 240, {pad_left: 319, pad_top: 239, pad_right: 0, pad_bottom: 0})).toBeNull();
  });
});
