/** Names may come from a current declaration; values only come from the record. */
export function parameterEvidence(record: Record<string, unknown> | undefined, names: string[] = []): string {
  if (record === undefined) return '历史未记录';
  const missing = names.filter(name => !Object.prototype.hasOwnProperty.call(record, name));
  return JSON.stringify(record, null, 2) + (missing.length ? `\n历史未记录：${missing.join(', ')}` : '');
}
