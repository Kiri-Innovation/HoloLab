declare const __EMBED_BUILD_ID__: string;

// A build-scoped key, not a render-scoped nonce: React rerenders must never
// navigate an already mounted viewer or reset its camera/time.
export const EMBED_BUILD_ID = typeof __EMBED_BUILD_ID__ === "string" ? __EMBED_BUILD_ID__ : "dev";

export function versionedViewerUrl(url: string, build = EMBED_BUILD_ID): string {
  const result = new URL(url, "http://viewer.local");
  result.searchParams.set("b", build);
  return `${result.pathname}${result.search}${result.hash}`;
}
