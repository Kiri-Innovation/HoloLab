import { readFile, writeFile, mkdir, rm, rename, realpath } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseDocument } from 'yaml';
import { unified } from 'unified';
import remarkParse from 'remark-parse';
import remarkGfm from 'remark-gfm';
import remarkStringify from 'remark-stringify';
import remarkRehype from 'remark-rehype';
import rehypeRaw from 'rehype-raw';
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize';
import rehypeStringify from 'rehype-stringify';
import { visit } from 'unist-util-visit';
import GithubSlugger from 'github-slugger';

const here = path.dirname(fileURLToPath(import.meta.url));
const website = path.resolve(here, '..');
const fail = (message) => { throw new Error(message); };
const object = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const word = (value) => typeof value === 'string' && value.trim().length > 0;
const slugPattern = /^[a-z0-9]+(?:-[a-z0-9]+)*(?:\/[a-z0-9]+(?:-[a-z0-9]+)*)*$/;
const plainText = (node) => node.value ?? node.children?.map(plainText).join('') ?? '';
const parser = unified().use(remarkParse).use(remarkGfm);
const json = async (file) => JSON.parse(await readFile(file, 'utf8'));

function metadata(meta, context) {
  if (!object(meta)) fail(`${context}: metadata must be a mapping`);
  for (const key of ['title', 'group']) if (!word(meta[key])) fail(`${context}: ${key} must be a non-empty string`);
  if (!Number.isSafeInteger(meta.order) || meta.order < 0) fail(`${context}: order must be a non-negative integer`);
}

export function frontmatter(text, context) {
  text = text.replace(/^\uFEFF/, '').replace(/\r\n/g, '\n');
  if (!text.startsWith('---')) return { body: text, meta: {} };
  const match = /^---\n([\s\S]*?)\n---(?:\n|$)/.exec(text);
  if (!match) fail(`${context}: malformed or unclosed frontmatter`);
  const doc = parseDocument(match[1], { uniqueKeys: true, strict: true });
  if (doc.errors.length || doc.warnings.length) fail(`${context}: ${[...doc.errors, ...doc.warnings].map(e => e.message).join('; ')}`);
  const meta = doc.toJS({ maxAliasCount: 0 });
  if (!object(meta)) fail(`${context}: frontmatter must be a mapping`);
  for (const key of Object.keys(meta)) if (!['title', 'group', 'order'].includes(key)) fail(`${context}: unknown frontmatter field ${key}`);
  // Partial frontmatter is allowed; every present field is validated, never silently replaced.
  for (const [key, value] of Object.entries(meta)) {
    if (key === 'order' ? !Number.isSafeInteger(value) || value < 0 : !word(value)) fail(`${context}: invalid frontmatter ${key}`);
  }
  return { body: text.slice(match[0].length), meta };
}

export async function syncDocs({ root = path.resolve(website, '..'), config = here, output = path.join(website, 'src/content/docs') } = {}) {
  root = await realpath(root);
  const [sources, sections, deferred] = await Promise.all(['docs-sources.json', 'docs-sections.json', 'docs-deferred.json'].map(name => json(path.join(config, name))));
  if (!Array.isArray(sources) || !Array.isArray(sections) || !Array.isArray(deferred)) fail('Configuration must contain arrays');
  const sectionIds = new Set(), routes = new Set(), seenPaths = new Set();
  for (const s of sections) {
    if (!object(s) || !/^[a-z]+$/.test(s.id) || !word(s.title) || !Number.isSafeInteger(s.order) || s.order < 0 || !/^\/docs(?:\/[a-z-]+)?$/.test(s.href)) fail('Invalid section');
    if (sectionIds.has(s.id) || routes.has(s.href)) fail(`Duplicate section: ${s.id}`);
    sectionIds.add(s.id); routes.add(s.href);
  }
  const sourcePath = (source) => {
    if (!word(source) || path.isAbsolute(source) || source.includes('\\') || source.split('/').some(s => !s || s === '.' || s === '..') || !source.endsWith('.md')) fail(`Unsafe source path: ${source}`);
    return path.resolve(root, source);
  };
  const deferredPaths = new Set(deferred.map(sourcePath));
  if (deferredPaths.size !== deferred.length) fail('Duplicate deferred source');
  const docs = [];
  for (const entry of sources) {
    if (!object(entry)) fail('Invalid source entry');
    for (const key of Object.keys(entry)) if (!['source', 'slug', 'section', 'title', 'group', 'order', 'diagrams'].includes(key)) fail(`Unknown source field: ${key}`);
    if (entry.diagrams !== undefined && (typeof entry.diagrams !== 'boolean' || !entry.source?.startsWith('website/content/'))) fail('Diagrams are only allowed in website-owned content');
    metadata(entry, entry.source);
    if (!word(entry.slug) || !slugPattern.test(entry.slug)) fail(`Invalid slug: ${entry.slug}`);
    if (!sectionIds.has(entry.section)) fail(`Unknown section: ${entry.section}`);
    const href = `/docs/${entry.slug}`;
    if (routes.has(href)) fail(`Duplicate or reserved slug: ${entry.slug}`);
    routes.add(href);
    const file = sourcePath(entry.source);
    if (seenPaths.has(file) || deferredPaths.has(file)) fail(`Duplicate or deferred source: ${entry.source}`);
    seenPaths.add(file);
    const actual = await realpath(file);
    if (actual !== file) fail(`Symlink source is not allowed: ${entry.source}`);
    const { body, meta } = frontmatter(await readFile(file, 'utf8'), entry.source);
    const resolved = { ...entry, ...meta };
    metadata(resolved, entry.source);
    const tree = parser.parse(body), slugger = new GithubSlugger(), headings = [];
    visit(tree, 'heading', node => {
      const title = plainText(node), id = slugger.slug(title);
      node.data = { hProperties: { id } };
      headings.push({ depth: node.depth, title, id });
    });
    docs.push({ ...resolved, href, file, tree, headings });
  }
  const byFile = new Map(docs.map(doc => [doc.file, doc]));
  const byHref = new Map(docs.map(doc => [doc.href, doc]));
  const withheld = [];
  function checkAnchor(target, hash, context) {
    if (hash && !target.headings.some(h => h.id === decodeURIComponent(hash))) fail(`${context}: missing heading #${hash}`);
  }
  for (const doc of docs) {
    // Resolve reference-style links through the same rules as inline links.
    const definitions = new Map();
    visit(doc.tree, 'definition', n => definitions.set(n.identifier.toLowerCase(), n));
    visit(doc.tree, (node, index, parent) => {
      if (node.type !== 'linkReference' && node.type !== 'imageReference') return;
      const def = definitions.get(node.identifier.toLowerCase());
      if (!def) fail(`${doc.source}: missing link definition ${node.identifier}`);
      parent.children[index] = node.type === 'linkReference'
        ? { type: 'link', url: def.url, title: def.title, children: node.children }
        : { type: 'image', url: def.url, title: def.title, alt: node.alt };
    });
    visit(doc.tree, (node, index, parent) => {
      if (!['link', 'image'].includes(node.type)) return;
      const url = node.url;
      if (/^(https?:|mailto:)/i.test(url)) return;
      if (node.type === 'image') fail(`${doc.source}: local image needs an explicit reviewed asset: ${url}`);
      if (url.startsWith('#')) { checkAnchor(doc, url.slice(1), doc.source); return; }
      if (url.startsWith('/')) {
        const [route, hash] = url.split('#');
        if (byHref.has(route)) checkAnchor(byHref.get(route), hash, doc.source);
        else if (route !== '/' && !sections.some(s => s.href === route)) fail(`${doc.source}: unknown route ${url}`);
        return;
      }
      if (/^[a-z][a-z0-9+.-]*:/i.test(url) || url.includes('?')) fail(`${doc.source}: unsupported link ${url}`);
      const [relative, hash] = url.split('#');
      const targetFile = path.resolve(path.dirname(doc.file), decodeURIComponent(relative));
      if (deferredPaths.has(targetFile)) {
        withheld.push({ source: doc.source, target: path.relative(root, targetFile) });
        parent.children[index] = { type: 'text', value: `${plainText(node)}（文档整理中）` };
        return;
      }
      const target = byFile.get(targetFile);
      if (!target) fail(`${doc.source}: link outside publication allowlist: ${url}`);
      checkAnchor(target, hash, doc.source);
      node.url = target.href + (hash ? `#${hash}` : '');
    });
    // Definitions are no longer needed and must not leak withheld destinations.
    doc.tree.children = doc.tree.children.filter(n => n.type !== 'definition');
    doc.markdown = unified().use(remarkStringify).use(remarkGfm).stringify(doc.tree);
    const schema = { ...defaultSchema, clobberPrefix: '', attributes: { ...defaultSchema.attributes, '*': [...(defaultSchema.attributes['*'] ?? []), 'id'] } };
    // Raw markup is opt-in per publication entry, restricted to our own content.
    // Sanitize AFTER parsing: no scripts, event handlers, style, foreignObject,
    // external SVG references or arbitrary classes that could alter the page UI.
    if (doc.diagrams) {
      schema.tagNames = [...defaultSchema.tagNames, 'figure', 'figcaption', 'svg', 'path'];
      schema.attributes = {
        ...schema.attributes,
        div: [['className', 'concept-flow', 'concept-card', 'concept-devices', 'concept-connection']],
        figure: [['className', 'concept-diagram'], 'ariaLabel'],
        svg: ['viewBox', 'ariaHidden'],
        path: ['d'],
      };
    }
    const renderer = unified().use(remarkRehype, { allowDangerousHtml: !!doc.diagrams });
    if (doc.diagrams) renderer.use(rehypeRaw);
    renderer.use(rehypeSanitize, schema).use(rehypeStringify);
    doc.html = renderer.stringify(await renderer.run(doc.tree));
  }
  const staging = `${output}.tmp-${process.pid}`;
  await rm(staging, { recursive: true, force: true });
  await mkdir(staging, { recursive: true });
  try {
    for (const doc of docs) {
      const destination = path.join(staging, `${doc.slug}.md`);
      await mkdir(path.dirname(destination), { recursive: true });
      await writeFile(destination, doc.markdown);
    }
    const published = docs.map(({ file, tree, markdown, ...doc }) => doc);
    await writeFile(path.join(staging, 'index.json'), JSON.stringify({ sections: sections.sort((a,b) => a.order-b.order), docs: published.sort((a,b) => a.order-b.order) }, null, 2));
    await rm(output, { recursive: true, force: true });
    await rename(staging, output);
  } finally { await rm(staging, { recursive: true, force: true }); }
  return { count: docs.length, withheld };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const result = await syncDocs();
    console.log(`Published ${result.count} allowlisted documents.`);
    for (const link of result.withheld) console.log(`Deferred link: ${link.source} → ${link.target} (rendered as text)`);
  } catch (error) { console.error(`Docs sync failed: ${error.message}`); process.exitCode = 1; }
}
