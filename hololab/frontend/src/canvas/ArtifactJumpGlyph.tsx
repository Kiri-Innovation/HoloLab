/** Shared "open artifact in Cocoder" mark: a rounded box with an outward arrow.
 * Match the source/reference controls: 12px, 2.2 stroke, inherited theme colour.
 */
export function ArtifactJumpGlyph() {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      data-hl-artifact-jump-icon=""
    >
      <path d="M10 4H5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h13a2 2 0 0 0 2-2v-5" />
      <path d="M14 3h7v7M21 3 10 14" />
    </svg>
  );
}
