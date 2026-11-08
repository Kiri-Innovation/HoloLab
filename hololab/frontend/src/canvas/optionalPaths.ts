/** Unset optional paths are absence, never null placeholders or browsing roots. */
export function omitEmptyOptionalPaths(
  specs: Record<string, { type?: string; optional?: boolean }> | undefined,
  params: Record<string, unknown>,
): Record<string, unknown> {
  return Object.fromEntries(Object.entries(params).filter(([name, value]) => {
    const spec = specs?.[name];
    return !(spec?.type === "path" && spec.optional === true &&
      (value == null || (typeof value === "string" && !value.trim())));
  }));
}
