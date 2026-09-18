// Tag-matching logic for typed port connections.
//
// This must stay in sync with the backend rules in
// hololab/gateway/workflows.py — ``tags_compatible`` for tag-set overlap
// (with the ``any`` wildcard), and ``ports_compatible`` for the full
// edge rule (tag overlap AND arrayed cardinality match).

export const ANY_TAG = "any";

export function tagsCompatible(a: string[], b: string[]): boolean {
  if (a.includes(ANY_TAG) || b.includes(ANY_TAG)) return true;
  const setA = new Set(a);
  for (const t of b) if (setA.has(t)) return true;
  return false;
}

// Full edge compatibility: tags overlap AND arrayed cardinality matches.
// ``arrayed<T>`` connects only to ``arrayed<T>``; ``T`` connects only to
// ``T``. Use an explicit ``arrayfy`` node to promote a scalar into an
// array (no implicit broadcasting — the graph should show the promotion).
export function portsCompatible(
  srcTags: string[],
  srcArrayed: boolean,
  tgtTags: string[],
  tgtArrayed: boolean,
): boolean {
  if (srcArrayed !== tgtArrayed) return false;
  return tagsCompatible(srcTags, tgtTags);
}

// Compute the effective ``arrayed`` state of one port on one graph node.
// Rule: manifest declaration OR (pack.arrayable AND node.arrayed_toggle).
// A port that is arrayed in the manifest stays arrayed regardless of the
// per-node checkbox (e.g. ``video-array-source.videos_dir``); ports that
// default to non-arrayed on an arrayable pack flip when the operator
// turns on the toggle.
export function effectivePortArrayed(
  portArrayed: boolean | undefined,
  packArrayable: boolean | undefined,
  nodeToggle: boolean | undefined,
): boolean {
  return Boolean(portArrayed) || (Boolean(packArrayable) && Boolean(nodeToggle));
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
