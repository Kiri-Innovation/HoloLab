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

## Documentation sources

- `scripts/docs-sources.json` is the publication allowlist. `source` is relative
  to the HoloLab repository root, not this directory. No directory globbing is used.
- `scripts/docs-sections.json` defines the ordered top-level sections.
- `scripts/docs-deferred.json` lists unpublished targets that may be mentioned
  by approved sources. It does not authorize reading or publishing their contents.
  A link to one becomes visible text with “文档整理中”; each conversion is logged.
- `scripts/sync-docs.mjs` reads the original Markdown on each sync and generates
  `src/content/docs/`. Generated Markdown, rendered HTML and navigation JSON are
  ignored by Git. Do not edit them or create a manually maintained copy of an
  existing document under `content/`.
- `content/use/` contains the original sources of the four new usage guides.

Source entries require `source`, `slug`, `section`, `title`, `group`, `order`.
Slugs support multiple lowercase path segments. Reserved section URLs, duplicate
sources/slugs, traversal and symlink sources are rejected.

Optional YAML frontmatter supports `title`, `group`, `order`. Present fields take
precedence over the mapping and are validated even if a fallback exists. Missing
fields use the mapping. Duplicate YAML keys, unknown fields, malformed YAML,
empty labels and non-integer/negative orders fail the sync. All six published
product documents carry frontmatter; mapping fallbacks remain supported.

Relative Markdown links (including reference-style links) are rewritten through
the allowlist. Published heading anchors are validated. Unknown local links,
unsupported protocols, and unreviewed local images fail the build. Fenced code
is preserved. Raw HTML is not enabled, and generated HTML is sanitized. The site
uses real anchors and native browser navigation, including deep URLs and fragments.
All documents are validated before replacing prior generated output; successful
sync removes stale generated pages.

## Current publication inventory

| Source | Public URL |
| --- | --- |
| `website/content/use/quick-start.md` | `/docs/quick-start` |
| `website/content/use/workflows.md` | `/docs/use/workflows` |
| `website/content/use/results.md` | `/docs/use/results` |
| `website/content/use/troubleshooting.md` | `/docs/use/troubleshooting` |
| `docs/artifacts.md` | `/docs/reference/artifacts` |
| `docs/writing-a-pack.md` | `/docs/build/writing-a-pack` |
| `docs/architecture.md` | `/docs/build/architecture` |
| `docs/type-ir.md` | `/docs/build/type-ir` |
| `docs/pack-spec.md` | `/docs/reference/pack-spec` |
| `docs/workflow-schema.md` | `/docs/reference/workflow-schema` |

The five reviewed development/reference documents are applied to their product
sources and published. The deferred list is empty. Links between the six product
documents resolve through the allowlist, including the artifact-to-Pack link.
`docs/reports/*`, `TODO`, `design-spec`, `cobrowser-integration`, and
`video-preview-transcoding` are not included. The root README and internal skill
are not imported. `review/` is ignored and never imported by the app or sync.

## Design and content provenance

`src/styles/tokens.css` is a snapshot of the product's
`hololab/frontend/src/theme/tokens.css`, which is imported by its `styles.css`.
Website layout styles are separate. Updating product tokens does not implicitly
change the website; review and update this snapshot when needed.

Theme options are light, dark, and system, persisted under the separate
`hololab.website.theme` key. The workflow illustration is rendered with HTML/CSS
and SVG; it is labeled as a diagram and contains no real device or project data.
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
The browser check covers all thirteen document/index routes in both themes, 404 UI, theme
persistence and system changes, relative links, fragments, browser history and
mobile navigation/overflow. It writes ignored PNGs under `test-results/`: home and quick-start in light/dark
themes, each of the five reviewed documents in both themes, plus mobile views.

## Deployment (Flops)

1. Check out the complete HoloLab repository. Building only a detached copy of
   `website/` will fail because the six `docs/*.md` files are source dependencies.
2. From `website/`, run `npm ci` and `npm run build` with Node.js 22. Website
   builds need neither Python, product frontend dependencies nor product submodules.
3. Serve `website/dist/` on `hololab.xenotech.studio` with HTTPS.
4. Configure history fallback for page URLs so a direct visit or refresh at
   `/docs/use/workflows` serves `index.html`. Missing assets should return 404.
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
    try_files $uri $uri/ /index.html;
}
```

This is a client-rendered static site: unknown page URLs display the app's 404
view after the fallback, so the HTTP response remains 200 unless the hosting
layer supplies route-aware 404 handling. It has no backend or product API proxy.
Canonical links use the public domain; route titles and descriptions update in
the browser. Server rendering and full-text search are not included in this batch.

## Product visual alignment

See [VISUAL-ALIGNMENT.md](VISUAL-ALIGNMENT.md) for the product palette, typography,
component sources, deliberate reading-layout exceptions and before/after decisions.
With preview running, `PRODUCT_URL=http://<product-host>:8828 npm run test:visual`
performs a read-only Gallery comparison and writes local comparison boards. It
blocks non-read HTTP requests and never opens an editable workflow. Reference
screenshots and measured styles stay under ignored `test-results/visual/`.
