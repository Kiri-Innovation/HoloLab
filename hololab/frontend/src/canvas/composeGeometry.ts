export type Transform = [number, number, number];
export type Clip = {
  id: string;
  url: string;
  width: number;
  height: number;
  duration: number;
};
// Match Python round() in the renderer, including half-to-even dimensions.
export function pixelRound(n: number) {
  const floor = Math.floor(n);
  return n - floor === 0.5 ? floor + (floor % 2) : Math.round(n);
}
export function layerRect(
  clip: Clip,
  canvas: { width: number; height: number },
  t: Transform,
) {
  const contain = Math.min(
    canvas.width / clip.width,
    canvas.height / clip.height,
  );
  return {
    x: t[0] * canvas.width,
    y: t[1] * canvas.height,
    width: Math.max(1, pixelRound(clip.width * contain * t[2])),
    height: Math.max(1, pixelRound(clip.height * contain * t[2])),
  };
}
export function activeAt(time: number, offset: number, duration: number) {
  return time >= offset && time < offset + duration;
}
export function moveTransform(
  t: Transform,
  dx: number,
  dy: number,
  displayedWidth: number,
  displayedHeight: number,
): Transform {
  return [t[0] + dx / displayedWidth, t[1] + dy / displayedHeight, t[2]];
}
export function resizeTransform(
  t: Transform,
  dx: number,
  dy: number,
  displayedWidth: number,
  displayedHeight: number,
): Transform {
  const ratio =
    1 +
    (dx * displayedWidth + dy * displayedHeight) /
      (displayedWidth ** 2 + displayedHeight ** 2);
  return [t[0], t[1], Math.max(0.001, t[2] * ratio)];
}
export function compositionError(
  transforms: unknown,
  offsets: unknown,
  count: number,
): string | null {
  if (!Array.isArray(transforms) || transforms.length !== count)
    return `transforms 层数必须为 ${count}`;
  if (!Array.isArray(offsets) || offsets.length !== count)
    return `offsets 层数必须为 ${count}`;
  for (let i = 0; i < count; i++) {
    const t = transforms[i];
    if (
      !Array.isArray(t) ||
      t.length !== 3 ||
      !t.every((v) => typeof v === "number" && Number.isFinite(v)) ||
      t[2] <= 0
    )
      return `第 ${i + 1} 层：transform 需要三个有限数，scale > 0`;
    if (
      typeof offsets[i] !== "number" ||
      !Number.isFinite(offsets[i]) ||
      offsets[i] < 0
    )
      return `第 ${i + 1} 层：offset 必须是 ≥ 0 的有限数`;
  }
  return null;
}
