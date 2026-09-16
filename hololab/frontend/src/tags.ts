// Tag-matching logic for typed port connections.
//
// This must stay in sync with the backend rule in
// hololab/gateway/workflows.py -> tags_compatible: two tag sets are
// compatible iff they share at least one tag.

export function tagsCompatible(a: string[], b: string[]): boolean {
  const setA = new Set(a);
  for (const t of b) if (setA.has(t)) return true;
  return false;
}

// Deterministic pastel per tag for the port dot colour. Same tag → same colour
// across every pack in the palette so the eye can quickly spot compatible
// ports.
export function colourForTag(tag: string): string {
  let h = 0;
  for (let i = 0; i < tag.length; i++) h = (h * 31 + tag.charCodeAt(i)) >>> 0;
  const hue = h % 360;
  return `hsl(${hue}, 55%, 50%)`;
}

export function firstTagColour(tags: string[], fallback = "var(--neutral)"): string {
  return tags.length > 0 ? colourForTag(tags[0]) : fallback;
}
