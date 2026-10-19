/** Workflow cosmetic state. Unknown names survive upgrades but are not rendered. */
export type ParameterPins = Record<string, string[]>;
export const parameterPinKey = (pack: { name: string; version: string }) => `${pack.name}@${pack.version}`;
export function toggleParameterPins(current: ParameterPins, key: string, names: string[]): ParameterPins {
  const old = current[key] ?? [];
  const remove = names.every(name => old.includes(name));
  const next = remove ? old.filter(name => !names.includes(name)) : [...new Set([...old, ...names])];
  const result = { ...current };
  if (next.length) result[key] = next;
  else delete result[key];
  return result;
}
