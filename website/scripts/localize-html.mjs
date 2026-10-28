// Emit language-specific heads for static hosting and crawlers, before JS runs.
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import path from 'node:path';
const root = new URL('../', import.meta.url);
const read = async name => JSON.parse(await readFile(new URL(name, root), 'utf8'));
const [{ docs, sections }, en, zh, labels] = await Promise.all(['src/content/docs/index.json', 'src/i18n/en.json', 'src/i18n/zh.json', 'src/i18n/metadata.json'].map(read));
const template = await readFile(new URL('dist/index.html', root), 'utf8');
const origin = 'https://hololab.xenotech.studio';
const escape = value => value.replaceAll('&', '&amp;').replaceAll('"', '&quot;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
let count = 0;
for (const locale of ['en', 'zh-CN']) {
  const dictionary = locale === 'en' ? en : zh;
  const prefix = locale === 'en' ? '' : '/zh-CN';
  const routes = [
    { href: `${prefix}/`, title: dictionary.homeTitle },
    ...sections.flatMap(s => [s.href, ...(s.aliases ?? [])].map(href => ({ href: prefix + href, title: dictionary[s.id] }))),
    ...docs.filter(d => d.locale === locale).map(d => ({
      href: d.href,
      title: locale === 'en' ? labels[d.title] ?? d.title : d.title,
      // Keep the in-progress installation paths crawlable in the static
      // artifact. React replaces this fallback when it hydrates the page.
      staticContent: d.locale === 'zh-CN' && d.slug === 'quick-start'
        ? `<main id="main" class="document"><article lang="zh-CN" class="prose">${d.html}</article></main>`
        : '',
    })),
  ];
  for (const route of routes) {
    const base = route.href.replace(/^\/zh-CN/, '') || '/';
    const title = `${route.title} — HoloLab`;
    const shell = route.staticContent
      ? template.replace('<div id="root"></div>', `<div id="root">${route.staticContent}</div>`)
      : template;
    const html = shell.replace(/<html lang="[^"]*">/, `<html lang="${locale}">`)
      .replace(/<title>[^<]*<\/title>/, `<title>${escape(title)}</title>`)
      .replace(/(<meta name="description" content=")[^"]*/, `$1${escape(route.href.endsWith('/') ? dictionary.intro : `${route.title} · HoloLab`)}`)
      .replace(/(<meta property="og:locale" content=")[^"]*/, `$1${locale === 'en' ? 'en_US' : 'zh_CN'}`)
      .replace(/(<link rel="canonical" href=")[^"]*/, `$1${origin}${route.href}`)
      .replace(/(<link rel="alternate" hreflang="(en|zh-CN|x-default)" href=")[^"]*/g, (_, start, language) => start + origin + (language === 'zh-CN' ? '/zh-CN' : '') + base);
    const destination = new URL(`dist${route.href}/index.html`.replace(/\/+/g, '/'), root);
    await mkdir(path.dirname(destination.pathname), { recursive: true });
    await writeFile(destination, html);
    count++;
  }
}
console.log(`Wrote localized HTML heads for ${count} routes.`);
