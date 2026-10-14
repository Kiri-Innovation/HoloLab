# Website languages

English is the editorial source. Update `content/en/` first, then the complete
Chinese counterpart under `content/zh/`. These are independent Markdown files,
not translation overlays. Keep code samples, product labels, and facts aligned.
Use [GLOSSARY.md](GLOSSARY.md) before editing either language. Do not normalize
spaces globally: Chinese punctuation and mixed Chinese/English text have different
spacing requirements.

## Files and publication

- `content/en/use/*.md`: six English usage guides, including their own diagram markup.
- `content/zh/use/*.md`: the six original Chinese guides, moved without content edits.
- `src/i18n/en.json`, `zh.json`: website UI dictionaries; `t('key')` is type-checked
  against the English keys. `metadata.json` localizes the existing repository
  navigation labels without modifying their source documents.
- `src/i18n/index.ts`: URL locale, path localization, and dictionary lookup. Real
  anchors reload the page, so there is no second client-side locale state to drift.
- `scripts/docs-sources.json`: one allowlisted entry per document and URL locale.
  `locale` is `en` or `zh-CN`; `contentLanguage` identifies the actual body language.
  `title`, `group`, and `order` in Markdown frontmatter override mapping defaults.
- `legacySource` preserves the original relative-link base of moved Chinese files.
  New links should be relative to that base until the aliases are deliberately retired.
  English documents resolve relative to their actual new location.
- `sync-docs.mjs` resolves links within the current locale, validates anchors,
  sanitizes opted-in website-owned diagrams, and emits separate generated copies.
  Build output is not an authoring source.

## URLs and preferences

English uses `/`, `/docs/<slug>`, `/docs/build/...`, and `/docs/reference/...`.
Chinese uses the same paths prefixed with `/zh-CN` (home: `/zh-CN/`). Slugs have
not changed: for example `/docs/use/workflows` remains valid.

The header switch preserves the page slug, query string, and corresponding heading
(by heading order in the paired guides). It stores the choice in
`hololab.website.locale`. Storage denial does not prevent URL-based switching.
Only `/` negotiates a language: saved choice first, then the browser's primary
language, then English. Explicit article URLs never redirect based on preferences.
Existing `/docs/...` links now show English; select 简体中文 to open the same article
under `/zh-CN/docs/...`. The theme preference remains independent.

Each page has a self-canonical URL, `en`, `zh-CN`, and `x-default` alternate links,
a matching `html lang`, and `og:locale` (`en_US` / `zh_CN`). `x-default` points to
English. Build-time `localize-html.mjs` emits these heads into 32 route HTML files,
so metadata is available before JavaScript. The app also updates it in development.
Serve generated route files before the SPA fallback; do not rewrite every URL
unconditionally to the root index. Page bodies remain client-rendered.

## Batch 2 — wait for approval

The six repository `docs/*.md` sources already contain mostly English bodies, despite
Chinese frontmatter; some passages and quoted UI labels are still Chinese. This batch leaves them untouched and keeps publishing them.
The Chinese website labels this fallback “英文原文”; the article has `lang="en"`.

For the next batch, create complete website-owned editions under
`content/en/build/`, `content/en/reference/`, `content/zh/build/`, and
`content/zh/reference/`. Review and adapt the existing English prose rather than
retranslate it; create the matching Chinese website editions. Keep repository
`docs/` as team documentation. Record each edition's upstream source and reviewed
commit so later product changes can be reconciled. Update the allowlist to use
those copies and remove the fallback notice once each pair is ready. This avoids
changing repository docs or introducing `docs/en/` just for website publishing.

## Checks

`npm test` checks synchronization, language isolation, link rewriting, and HTML
sanitization. `npm run build` checks TypeScript and generates static metadata.
`npm run test:metadata` checks the built HTML heads (or HTTP responses when
`WEBSITE_URL` is set). `npm run test:browser` checks both languages, themes, page-preserving switching,
preferences, denied storage, internal links, metadata, and 320/390px layouts.
Screenshots go to ignored `test-results/i18n/`.
