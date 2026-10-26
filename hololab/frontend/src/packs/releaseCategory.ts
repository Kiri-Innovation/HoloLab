/** Older registry records keep the manifest under identity evidence. */
export function releaseCategory(release?: {category?: unknown; evidence?: {manifest?: {normalized?: {category?: unknown}}}}): string[] {
  const value = release?.category ?? release?.evidence?.manifest?.normalized?.category;
  if (typeof value === 'string') return value.split('/').map(part => part.trim()).filter(Boolean);
  if (Array.isArray(value)) return value.filter((part): part is string => typeof part === 'string' && part.length > 0);
  return [];
}
