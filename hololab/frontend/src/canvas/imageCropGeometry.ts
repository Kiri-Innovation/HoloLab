export const padKeys = ['pad_left', 'pad_top', 'pad_right', 'pad_bottom'] as const;
export type Pads = Record<typeof padKeys[number], number>;
export type CropRect = { x: number; y: number; width: number; height: number };
export type CropHandle = 'move' | 'n' | 'ne' | 'e' | 'se' | 's' | 'sw' | 'w' | 'nw';
// Python crop.py rounds halves away from zero, unlike Math.round(-0.5).
export const pixelPadding = (v: number) => Math.sign(v) * Math.floor(Math.abs(v) + 0.5);
export function cropRect(w: number, h: number, p: Pads): CropRect {
  const [l, t, r, b] = padKeys.map(k => pixelPadding(p[k]));
  return { x: l, y: t, width: w - l - r, height: h - t - b };
}
export function cropError(w: number, h: number, p: Pads): string | null {
  if (!padKeys.every(k => Number.isFinite(p[k]))) return 'Padding 必须是有限数值';
  const [l, t, r, b] = padKeys.map(k => pixelPadding(p[k]));
  if (Math.max(0, l) + Math.max(0, r) >= w || Math.max(0, t) + Math.max(0, b) >= h)
    return '至少保留 1 × 1 原图像素；扩边不能补救裁空';
  const rect = cropRect(w, h, p);
  if (rect.width > 100000 || rect.height > 100000 || rect.width * rect.height > 100000000)
    return '输出超出后端尺寸限制';
  return null;
}
export function dragCrop(w: number, h: number, start: Pads, handle: CropHandle, dx: number, dy: number): Pads {
  const rect = cropRect(w, h, start);
  let l = rect.x, t = rect.y, r = l + rect.width, b = t + rect.height;
  dx = pixelPadding(dx); dy = pixelPadding(dy);
  if (handle === 'move') {
    // Keep at least one original pixel on each axis, including outward padding.
    dx = Math.max(1 - r, Math.min(w - 1 - l, dx));
    dy = Math.max(1 - b, Math.min(h - 1 - t, dy));
    l += dx; r += dx; t += dy; b += dy;
  } else {
    if (handle.includes('w')) l = Math.min(l + dx, Math.min(w, r) - 1);
    if (handle.includes('e')) r = Math.max(r + dx, Math.max(0, l) + 1);
    if (handle.includes('n')) t = Math.min(t + dy, Math.min(h, b) - 1);
    if (handle.includes('s')) b = Math.max(b + dy, Math.max(0, t) + 1);
  }
  const next = { pad_left: l, pad_top: t, pad_right: w - r, pad_bottom: h - b };
  return cropError(w, h, next) ? start : next;
}
export function cropViewport(w: number, h: number, p: Pads): CropRect {
  const r = cropRect(w, h, p), margin = Math.max(w, h) * 0.15;
  const x = Math.min(0, r.x) - margin, y = Math.min(0, r.y) - margin;
  return { x, y, width: Math.max(w, r.x + r.width) + margin - x, height: Math.max(h, r.y + r.height) + margin - y };
}
