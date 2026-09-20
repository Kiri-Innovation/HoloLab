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
// Asymmetric exception: arrayed source → explicit scalar target is allowed
// (the scalar port broadcasts the full parent handle to every shard).
// A scalar source (portScalar=true → srcArrayed=false) into an arrayed
// target is rejected — the fan-out zip would find 0 sub-elements.
export function portsCompatible(
  srcTags: string[],
  srcArrayed: boolean,
  tgtTags: string[],
  tgtArrayed: boolean,
  tgtScalar?: boolean,
): boolean {
  if (srcArrayed !== tgtArrayed) {
    // Allow arrayed source → explicit scalar target (broadcast semantic).
    if (!(srcArrayed && tgtScalar)) return false;
  }
  return tagsCompatible(srcTags, tgtTags);
}

// Compute the effective ``arrayed`` state of one port on one graph node.
// Rule: ``portScalar`` wins unconditionally (always non-arrayed); otherwise
// manifest declaration OR (pack.arrayable AND node.arrayed_toggle).
export function effectivePortArrayed(
  portArrayed: boolean | undefined,
  packArrayable: boolean | undefined,
  nodeToggle: boolean | undefined,
  portScalar?: boolean,
): boolean {
  if (portScalar) return false;
  return Boolean(portArrayed) || (Boolean(packArrayable) && Boolean(nodeToggle));
}

// Effective dim-label list for one port on one graph node. Depth =
// list length. Empty = scalar. For arrayable packs promoted via
// ``arrayed_toggle`` (no manifest declaration), the promotion adds one
// unlabeled level at the outer end so callers still know "there is a
// dim here, just no name for it". Manifest declarations with
// ``dim_labels`` win as-is.
export function effectivePortDimLabels(
  portArrayed: boolean | undefined,
  portDimLabels: string[] | undefined,
  packArrayable: boolean | undefined,
  nodeToggle: boolean | undefined,
  portScalar?: boolean,
): string[] {
  if (portScalar) return [];
  const arrayed = Boolean(portArrayed) || (Boolean(packArrayable) && Boolean(nodeToggle));
  if (!arrayed) return [];
  const declared = portDimLabels ?? [];
  if (declared.length > 0) return declared.slice();
  // Legacy bool ``arrayed=true`` with no dim_labels — one unlabeled level.
  // Or arrayable-pack promotion where the manifest is silent — same.
  return [""];
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
