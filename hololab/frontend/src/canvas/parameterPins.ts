/** Workflow cosmetic state. Explicit empty arrays override pack defaults. */
export type ParameterPins = Record<string, string[]>;
type PinPack = { name: string; version: string; params: Record<string, { pinned?: boolean; type?: string; items?: string | null }> };
export const parameterPinKey = (pack: { name: string; version: string }) => `${pack.name}@${pack.version}`;
export function effectiveParameterPins(current: ParameterPins, pack: PinPack): string[] {
  const key = parameterPinKey(pack);
  if (Object.prototype.hasOwnProperty.call(current, key)) return current[key];
  const defaults = Object.keys(pack.params).filter(name => pack.params[name].pinned === true);
  // The transform editor is one atomic row, including when a pack defaults one member.
  const group = ["x", "y", "scale"];
  if (key === "vec3-array-source@0.1.0"
      && group.every(name => pack.params[name]?.type === "array" && pack.params[name]?.items === "float")
      && group.some(name => defaults.includes(name))) {
    return Object.keys(pack.params).filter(name => defaults.includes(name) || group.includes(name));
  }
  return defaults;
}
export function toggleParameterPins(current: ParameterPins, key: string, names: string[], effective: string[] = []): ParameterPins {
  const old = Object.prototype.hasOwnProperty.call(current, key) ? current[key] : effective;
  const remove = names.every(name => old.includes(name));
  const next = remove ? old.filter(name => !names.includes(name)) : [...new Set([...old, ...names])];
  return { ...current, [key]: next };
}
