# HoloLab website

Public introduction and documentation for `https://hololab.xenotech.studio`.
This is an independent Vite 5 / React 18 / TypeScript 5 application. It does not
start, rebuild or call the HoloLab product service.

## Development

Use Node.js 22 and run from this directory:

```bash
npm ci
npm run dev
```

Development listens on `127.0.0.1:5199` with `strictPort`; an occupied port causes
an error rather than silently moving. For a remote preview, use an SSH tunnel or
explicitly supply the desired host. No product port or process is changed.

```bash
npm run build
npm run preview
```

Preview uses `127.0.0.1:5200`, also with strict port selection. Build output is
`dist/`. Source Markdown edits require `npm run sync:docs` or a dev-server restart;
`predev` and `prebuild` always synchronize before starting.

## Documentation sources and languages

See [i18n/README.md](i18n/README.md) for authoring, language routes, metadata,
and the next translation batch. [i18n/GLOSSARY.md](i18n/GLOSSARY.md) fixes terminology.

Six usage guides have complete English and Chinese copies under `content/en/use/`
and `content/zh/use/`. Six repository `docs/*.md` files remain read-only publication
sources for development/reference pages pending batch 2. The allowlist is
`scripts/docs-sources.json`; sections and deferred targets have separate mappings.
Frontmatter `title`, `group`, and `order` override mapping defaults.

Generated files in `src/content/docs/` are ignored. The sync validates publication
boundaries and heading anchors, rewrites links to the current language, and removes
stale generated pages only after validation succeeds. Raw diagram HTML/SVG is
opt-in for website-owned sources and sanitized after parsing. Scripts, handlers,
and arbitrary styles are not allowed.

## Design and content provenance

`src/styles/tokens.css` is a snapshot of the product's
`hololab/frontend/src/theme/tokens.css`, which is imported by its `styles.css`.
Website layout styles are separate. Updating product tokens does not implicitly
change the website; review and update this snapshot when needed.

Theme options are light, dark, and system, persisted under the separate
`hololab.website.theme` key. Documentation diagrams use HTML/CSS and SVG. The homepage uses theme-matched
product screenshots with device identifiers and source previews hidden.
The homepage follows the approved draft with two factual qualifications: Pack
progress support and the Flops Cobrowser prerequisite for file-location jumps.

The usage guides were checked against the CLI, Gallery, PackPalette,
NodeInspector, WorkflowToolbar, useDraftAutosave/SaveStatusPill, RecentJobsPanel,
JobLogModal, RunsPanel, nodeStatus and OpenArtifactLocationButton implementations.
They do not claim that software installation, an algorithm environment or a live
workflow was exercised as part of website verification.

## Verification

```bash
npm test
npx playwright install chromium
# With npm run dev running in another terminal:
npm run test:browser
# Or, against the production preview:
WEBSITE_URL=http://127.0.0.1:5200 npm run test:browser
```

Sync tests use temporary fixtures, including invalid metadata, publication
boundaries, nested links/anchors, raw HTML, source removal and error preservation.
The browser check covers 24 article routes and 6 section routes, both locales,
theme switching, saved/browser language preferences, denied localStorage, same-page
language switching, translated fragments, links, 404 UI, and 320/390px overflow.
It writes ignored screenshots under `test-results/i18n/`.

## Deployment (Flops)

1. Check out the complete HoloLab repository. Building only a detached copy of
   `website/` will fail because the six `docs/*.md` files are source dependencies.
2. From `website/`, run `npm ci` and `npm run build` with Node.js 22. Website
   builds need neither Python, product frontend dependencies nor product submodules.
3. Serve `website/dist/` on `hololab.xenotech.studio` with HTTPS.
4. Serve generated route-specific HTML first, then use a history fallback for
   unknown page URLs. Missing assets should return 404.
5. Cache hashed `/assets/*` files immutably; keep `index.html` revalidated.

Example Nginx location configuration inside the domain's HTTPS server:

```nginx
root /path/to/deployment/dist;
index index.html;
location /assets/ {
    try_files $uri =404;
    add_header Cache-Control "public, max-age=31536000, immutable";
}
location = /index.html {
    add_header Cache-Control "no-cache";
}
location / {
    try_files $uri $uri/index.html $uri/ /index.html;
}
```

This is a client-rendered static site: unknown page URLs display the app's 404
view after the fallback, so the HTTP response remains 200 unless the hosting
layer supplies route-aware 404 handling. It has no backend or product API proxy.
Canonical links use the public domain. Route-specific HTML includes localized
titles, descriptions, canonical/alternate links, and language metadata before JS runs. Server rendering and full-text search are not included in this batch.

## Product visual alignment

See [VISUAL-ALIGNMENT.md](VISUAL-ALIGNMENT.md) for the product palette, typography,
component sources, deliberate reading-layout exceptions and before/after decisions.
With preview running, `PRODUCT_URL=http://<product-host>:8828 npm run test:visual`
performs a read-only Gallery comparison and writes local comparison boards. It
blocks non-read HTTP requests and never opens an editable workflow. Reference
screenshots and measured styles stay under ignored `test-results/visual/`.
