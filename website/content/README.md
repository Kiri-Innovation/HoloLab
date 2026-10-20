# Browser-specific paragraphs

Use `<div class="flops-only">` for content shown only when CoBrowser's reference bridge is available, and `<div class="no-flops-only">` for ordinary browsers. Leave blank lines inside the wrapper so Markdown (including inline code) is parsed:

```markdown
<div class="flops-only">

Your paragraph with `inline code`.

</div>
```

Set `"environmentContent": true` on that document's entries in `scripts/docs-sources.json` (both languages). With `"screenshots": true`, the same classes also work directly on `figure`, `picture`, and `img` elements. Prefer wrapping a whole figure when its caption is also environment-specific. Only these two classes are permitted by this option; HTML remains sanitized. Keep each language's paragraph in its own Markdown file.

The website matches the product's `flopsInsertReferenceAvailable()` check: `window.flops.version === 1` and a callable `insertReference`. An empty object is not sufficient. CoBrowser injects the bridge before page scripts; the website checks before React mounts. Flops-only content is hidden by default, avoiding a flash in ordinary browsers. This is presentation, not access control.

The current build emits localized HTML heads and a client-rendered document body. Paragraphs remain in the generated content bundle and rendered DOM, including when hidden; static route HTML has no document body. No-JavaScript readers cannot read the page body under the existing architecture.

## Navigation metadata

The `group` in `scripts/docs-sources.json` controls website navigation, overriding source frontmatter. Keep website-owned Markdown frontmatter in sync. Shared documents under the repository’s `docs/` keep their own metadata; website grouping does not require changing those files.
