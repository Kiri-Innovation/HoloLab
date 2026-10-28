// Render every public route into its route file, then give each page the
// locale-specific metadata that static hosts and crawlers need.
import { execFile } from 'node:child_process';
import { readFile, writeFile, mkdir, rm } from 'node:fs/promises';
import { promisify } from 'node:util';
import path from 'node:path';

const exec = promisify(execFile);
const root = new URL('../', import.meta.url);
const rootPath = path.resolve(new URL('.', root).pathname);
const read = async name => JSON.parse(await readFile(new URL(name, root), 'utf8'));
const [{ docs, sections }, en, zh, labels] = await Promise.all([
  'src/content/docs/index.json', 'src/i18n/en.json', 'src/i18n/zh.json', 'src/i18n/metadata.json',
].map(read));
const template = await readFile(new URL('dist/index.html', root), 'utf8');
const origin = 'https://hololab.xenotech.studio';
const ssrDirectory = path.join(rootPath, '.prerender-ssr');
const serverEntry = path.join(ssrDirectory, 'entry-server.js');
const escape = value => value.replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');

await rm(ssrDirectory, { recursive: true, force: true });
await exec(process.execPath, [
  'node_modules/vite/bin/vite.js', 'build', '--ssr', 'src/entry-server.tsx', '--outDir', '.prerender-ssr',
], { cwd: rootPath });

const render = async route => {
  // A fresh Node process lets module-level route data match this route while
  // keeping the browser bundle free of prerender-only code.
  const program = `globalThis.__HOLOLAB_PATHNAME__=${JSON.stringify(route)}; import(${JSON.stringify(serverEntry)}).then(m => process.stdout.write(m.render()));`;
  const { stdout } = await exec(process.execPath, ['--input-type=module', '--eval', program], { cwd: rootPath, maxBuffer: 16 * 1024 * 1024 });
  return stdout;
};

const routes = [];
for (const locale of ['en', 'zh-CN']) {
  const dictionary = locale === 'en' ? en : zh;
  const prefix = locale === 'en' ? '' : '/zh-CN';
  routes.push(
    { href: `${prefix}/`, title: dictionary.homeTitle, locale, dictionary },
    ...sections.flatMap(s => [s.href, ...(s.aliases ?? [])].map(href => ({ href: prefix + href, title: dictionary[s.id], locale, dictionary }))),
    ...docs.filter(d => d.locale === locale).map(d => ({
      href: d.href,
      title: locale === 'en' ? labels[d.title] ?? d.title : d.title,
      locale,
      dictionary,
    })),
  );
}

for (const route of routes) {
  const base = route.href.replace(/^\/zh-CN/, '') || '/';
  const body = await render(route.href);
  const html = template.replace('<div id="root"></div>', `<div id="root">${body}</div>`)
    .replace(/<html lang="[^"]*">/, `<html lang="${route.locale}">`)
    .replace(/<title>[^<]*<\/title>/, `<title>${escape(`${route.title} — HoloLab`)}</title>`)
    .replace(/(<meta name="description" content=")[^"]*/, `$1${escape(route.href.endsWith('/') ? route.dictionary.intro : `${route.title} · HoloLab`)}`)
    .replace(/(<meta property="og:locale" content=")[^"]*/, `$1${route.locale === 'en' ? 'en_US' : 'zh_CN'}`)
    .replace(/(<link rel="canonical" href=")[^"]*/, `$1${origin}${route.href}`)
    .replace(/(<link rel="alternate" hreflang="(en|zh-CN|x-default)" href=")[^"]*/g, (_, start, language) => start + origin + (language === 'zh-CN' ? '/zh-CN' : '') + base);
  const destination = new URL(`dist${route.href}/index.html`.replace(/\/+/g, '/'), root);
  await mkdir(path.dirname(destination.pathname), { recursive: true });
  await writeFile(destination, html);
}

const publicDocs = docs.map(doc => ({ ...doc, title: doc.locale === 'en' ? labels[doc.title] ?? doc.title : doc.title }));
const llms = [
  '# HoloLab documentation', '', '> Official documentation for HoloLab visual workflows.', '',
  ...publicDocs.map(doc => `- [${doc.title}](${origin}${doc.href}): ${doc.headings.find(heading => heading.depth === 2)?.title ?? doc.title}`), '',
].join('\n');
const sitemap = `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${routes.map(route => `  <url><loc>${origin}${route.href}</loc></url>`).join('\n')}\n</urlset>\n`;
await writeFile(new URL('dist/llms.txt', root), llms);
await writeFile(new URL('dist/sitemap.xml', root), sitemap);
await rm(ssrDirectory, { recursive: true, force: true });
console.log(`Prerendered ${routes.length} localized routes and wrote llms.txt and sitemap.xml.`);
