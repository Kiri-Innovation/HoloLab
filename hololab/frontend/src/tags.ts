// Tag-matching logic for typed port connections.
//
// This must stay in sync with the backend rules in
// hololab/gateway/workflows.py — ``tags_compatible`` for tag-set overlap
// (with the ``any`` wildcard), and ``ports_compatible`` for the full
// edge rule (tag overlap AND arrayed cardinality match).

export const ANY_TAG = "any";

export function tagsCompatible(a: string[], b: string[]): boolean {
  if ([...a,...b].some(t=>t === "image_sequence" || t === "frame_sequence")) return false;
  if (a.includes(ANY_TAG) || b.includes(ANY_TAG)) return true;
  const setA = new Set(a);
  for (const t of b) if (setA.has(t)) return true;
  return false;
}

// Match tags and collection structure. Broadcasting preserves the invocation
// contract; it does not permit an array where a single image is declared.
export function portsCompatible(
  srcTags: string[], srcArrayed: boolean, tgtTags: string[], tgtArrayed: boolean,
  _tgtScalar?: boolean, srcDims?: string[], tgtDims?: string[], requireAxisLabelMatch = true,
): boolean {
  if (!tagsCompatible(srcTags, tgtTags)) return false;
  if ((!srcArrayed && srcTags.includes(ANY_TAG)) || (!tgtArrayed && tgtTags.includes(ANY_TAG))) return true;
  if (srcArrayed !== tgtArrayed) return false;
  if (!srcArrayed || srcDims === undefined || tgtDims === undefined) return true;
  const a = srcDims.length ? srcDims : [""];
  const b = tgtDims.length ? tgtDims : [""];
  // An `any` leaf may represent a nested generic value, after the explicitly
  // declared outer dimensions have matched (same as backend compatible()).
  for (let i = 0; i < Math.min(a.length, b.length); i++) {
    if (requireAxisLabelMatch && a[i] && b[i] && a[i] !== b[i]) return false;
  }
  return a.length === b.length || (a.length < b.length ? srcTags : tgtTags).includes(ANY_TAG);
}

// A node toggle adds one collection around the single-invocation declaration.
// scalar means broadcast that complete input unchanged, including its arrays.
export function effectivePortArrayed(
  portArrayed: boolean | undefined, packArrayable: boolean | undefined,
  nodeToggle: boolean | undefined, portScalar?: boolean,
): boolean {
  return Boolean(portArrayed) || (!portScalar && Boolean(packArrayable) && Boolean(nodeToggle));
}

export function effectivePortDimLabels(
  portArrayed: boolean | undefined, portDimLabels: string[] | undefined,
  packArrayable: boolean | undefined, nodeToggle: boolean | undefined, portScalar?: boolean,
): string[] {
  const invocation = portArrayed ? (portDimLabels?.length ? [...portDimLabels] : [""]) : [];
  return packArrayable && nodeToggle && !portScalar ? ["", ...invocation] : invocation;
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
