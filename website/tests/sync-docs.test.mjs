import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, readFile, rm, symlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { syncDocs, frontmatter } from '../scripts/sync-docs.mjs';

async function fixture(t) {
  const root = await mkdtemp(path.join(tmpdir(), 'hololab-docs-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const config = path.join(root, 'config'), output = path.join(root, 'output');
  await mkdir(config); await mkdir(path.join(root, 'docs'));
  const sources = [
    { source: 'docs/a.md', slug: 'use/a', section: 'use', title: 'A', group: 'Start', order: 10 },
    { source: 'docs/b.md', slug: 'reference/nested/b', section: 'use', title: 'B', group: 'Start', order: 20 },
  ];
  const write = (file, value) => writeFile(path.join(root, file), value);
  const configure = async (entries = sources, deferred = []) => {
    await write('config/docs-sources.json', JSON.stringify(entries));
    await write('config/docs-sections.json', JSON.stringify([{ id: 'use', title: '使用', href: '/docs', order: 10 }]));
    await write('config/docs-deferred.json', JSON.stringify(deferred));
  };
  await configure();
  await write('docs/a.md', '# A\n\n[Next](b.md#heading)\n\n[Again][b]\n\n[b]: b.md#heading\n');
  await write('docs/b.md', '# B\n\n## Heading\n\n## Heading\n');
  return { root, output, config, sources, write, configure, run: () => syncDocs({ root, output, config }), index: async () => JSON.parse(await readFile(path.join(output,'index.json'), 'utf8')) };
}

test('nested slugs, reference links, stable duplicate heading IDs and metadata', async t => {
  const f = await fixture(t);
  await f.write('docs/b.md', '---\ntitle: 新标题\ngroup: 新分组\norder: 2\n---\n# B\n\n## Heading\n\n## Heading\n');
  await f.run();
  const { docs } = await f.index();
  assert.equal(docs[0].title, '新标题');
  assert.equal(docs[0].group, '新分组');
  assert.match(docs[0].html, /id="heading-1"/);
  assert.equal((docs[1].html.match(/href="\/docs\/reference\/nested\/b#heading"/g) ?? []).length, 2);
  assert.match(await readFile(path.join(f.output, 'reference/nested/b.md'),'utf8'), /Heading/);
});

test('bad frontmatter is never silently ignored', () => {
  for (const source of [
    '---\ntitle: [\n---\n# X', '---\ntitle: A\n# unclosed',
    '---\ntitle: A\ntitle: B\n---\n', '---\norder: "10"\n---\n',
    '---\ngroup: false\n---\n', '---\ntitle: null\n---\n',
    '---\n- A\n---\n', '---\norderr: 10\n---\n',
    '---\norder: -1\n---\n',
  ]) assert.throws(() => frontmatter(source, 'test.md'));
});

for (const [label, change, pattern] of [
  ['duplicate slug', s => { s[1].slug = s[0].slug; }, /Duplicate/],
  ['reserved route', s => { s[0].slug = '../'; }, /Invalid slug/],
  ['path traversal', s => { s[0].source = '../secret.md'; }, /Unsafe source/],
  ['unknown section', s => { s[0].section = 'private'; }, /Unknown section/],
  ['missing source', s => { s[0].source = 'docs/missing.md'; }, /ENOENT/],
  ['invalid order', s => { s[0].order = '10'; }, /order/],
]) test(`reject ${label}`, async t => {
  const f = await fixture(t); change(f.sources); await f.configure(); await assert.rejects(f.run(), pattern);
});

test('allowlist blocks unpublished links, invalid anchors and dangerous schemes', async t => {
  const f = await fixture(t);
  for (const url of ['secret.md', 'b.md#missing', '/docs/not-real', 'javascript:alert%281%29', 'data:text/html,hello']) {
    await f.write('docs/a.md', `# A\n\n[No](${url})`);
    await assert.rejects(f.run());
  }
});

test('deferred reference has a visible notice and never copies its target', async t => {
  const f = await fixture(t);
  await f.write('docs/private.md', '# PRIVATE MARKER');
  await f.write('docs/a.md', '# A\n\n[Pending][p]\n\n[p]: private.md');
  await f.configure(f.sources, ['docs/private.md']);
  assert.equal((await f.run()).withheld.length, 1);
  const index = JSON.stringify(await f.index());
  assert.match(index, /文档整理中/);
  assert.doesNotMatch(index, /PRIVATE MARKER|private.md/);
});

test('raw HTML is not executed and code fences are preserved', async t => {
  const f = await fixture(t);
  await f.write('docs/a.md', '# A\n\n<script>alert(1)</script>\n\n```html\n<script>example</script>\n```');
  await f.run();
  const doc = (await f.index()).docs[0];
  assert.doesNotMatch(doc.html, /<script>/);
  assert.match(doc.html, /&#x3C;script>/);
});

test('validation failure preserves prior output; removing a source removes generated content', async t => {
  const f = await fixture(t);
  await f.run();
  const before = await readFile(path.join(f.output, 'index.json'),'utf8');
  await f.write('docs/a.md', '# A\n\n[bad](secret.md)');
  await assert.rejects(f.run());
  assert.equal(await readFile(path.join(f.output, 'index.json'),'utf8'), before);
  await f.write('docs/a.md', '# A');
  await f.configure(f.sources.slice(0,1));
  await f.run();
  await assert.rejects(readFile(path.join(f.output, 'reference/nested/b.md')), /ENOENT/);
});

test('symlink cannot smuggle a source into the allowlist', async t => {
  const f = await fixture(t);
  await f.write('secret.md', '# SECRET');
  await rm(path.join(f.root, 'docs/a.md'));
  await symlink(path.join(f.root, 'secret.md'), path.join(f.root, 'docs/a.md'));
  await assert.rejects(f.run(), /Symlink/);
});

test('diagram HTML/SVG is opt-in, website-owned and sanitized after parsing', async t => {
  const f = await fixture(t);
  await mkdir(path.join(f.root, 'website/content'), { recursive: true });
  const source = 'website/content/intro.md';
  await f.write(source, `# Intro

<figure class="concept-diagram" aria-label="System"><div class="concept-flow"><div class="concept-card">Gateway</div><svg viewBox="0 0 24 24" aria-hidden="true" onload="alert(1)"><path d="M3 12h18" /><script>alert(1)</script><foreignObject><iframe src="https://example.org"></iframe></foreignObject><use href="https://example.org/evil.svg#x" /></svg></div><figcaption>Caption</figcaption></figure>

<div class="header" style="position:fixed" onclick="alert(1)">Bad styling</div>
<a href="javascript:alert(1)">Bad link</a>
<img src="x" onerror="alert(1)">
`);
  const entry = { ...f.sources[0], source };
  await f.configure([entry]);
  await f.run();
  assert.doesNotMatch((await f.index()).docs[0].html, /<svg|<figure|concept-card/);
  await f.configure([{ ...entry, diagrams: true }]);
  await f.run();
  const html = (await f.index()).docs[0].html;
  assert.match(html, /<figure class="concept-diagram" aria-label="System">/);
  assert.match(html, /<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12h18"><\/path>/);
  assert.match(html, /<figcaption>Caption<\/figcaption>/);
  assert.doesNotMatch(html, /<script|<iframe|<foreignObject|<use|onload|onclick|onerror|style=|class="header"|javascript:/);
  await f.configure([{ ...f.sources[0], diagrams: true }]);
  await assert.rejects(f.run(), /only allowed in website-owned/);
});

test('localized copies resolve links within their language and keep separate output files', async t => {
  const f = await fixture(t);
  const entries = ['en', 'zh-CN'].flatMap(locale => f.sources.map(s => ({ ...s, locale, contentLanguage: 'en' })));
  await f.configure(entries);
  await f.run();
  const { docs } = await f.index();
  assert.equal(docs.length, 4);
  for (const locale of ['en', 'zh-CN']) {
    const prefix = locale === 'en' ? '' : '/zh-CN';
    const a = docs.find(d => d.slug === 'use/a' && d.locale === locale);
    assert.ok(a.html.includes(`href="${prefix}/docs/reference/nested/b#heading"`));
    assert.match(await readFile(path.join(f.output, locale, 'use/a.md'), 'utf8'), /Next/);
  }
  await f.configure([...entries, entries[0]]);
  await assert.rejects(f.run(), /Duplicate/);
});

test('moved Chinese sources keep legacy relative links and localize absolute doc links', async t => {
  const f = await fixture(t);
  await mkdir(path.join(f.root, 'website/content/zh/use'), { recursive: true });
  await f.write('website/content/zh/use/a.md', '# A\n\n[Relative](../../../docs/b.md#heading)\n\n[Absolute](/docs/reference/nested/b#heading)\n');
  await f.configure([
    { ...f.sources[0], source: 'website/content/zh/use/a.md', legacySource: 'website/content/use/a.md', locale: 'zh-CN' },
    { ...f.sources[1], locale: 'zh-CN' },
  ]);
  await f.run();
  const { docs } = await f.index();
  assert.equal((docs[0].html.match(/href="\/zh-CN\/docs\/reference\/nested\/b#heading"/g) ?? []).length, 2);
});
