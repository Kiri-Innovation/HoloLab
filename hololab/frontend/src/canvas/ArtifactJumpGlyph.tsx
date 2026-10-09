/** Shared "open artifact in Cocoder" mark: a folder with a simple rightward arrow.
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
      <path d="M20 9V8a2 2 0 0 0-2-2h-7L9 3H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-1" />
      <path d="M12 13h10m-4-4 4 4-4 4" />
    </svg>
  );
}
