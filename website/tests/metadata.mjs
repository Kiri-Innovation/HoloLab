import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
const read = async name => readFile(new URL(`../${name}`, import.meta.url), 'utf8');
const { docs, sections } = JSON.parse(await read('src/content/docs/index.json'));
for (const locale of ['en', 'zh-CN']) {
  const prefix = locale === 'en' ? '' : '/zh-CN';
  for (const route of [prefix + '/', ...sections.flatMap(s => [s.href, ...(s.aliases ?? [])].map(href => prefix + href)), ...docs.filter(d => d.locale === locale).map(d => d.href)]) {
    const html = process.env.WEBSITE_URL ? await (await fetch(process.env.WEBSITE_URL + route)).text() : await read(`dist${route.replace(/\/$/, '')}/index.html`);
    assert.ok(html.includes(`<html lang="${locale}">`), route);
    assert.ok(html.includes(`rel="canonical" href="https://hololab.xenotech.studio${route}"`), route);
    assert.ok(html.includes(`hreflang="zh-CN" href="https://hololab.xenotech.studio/zh-CN${route.replace(/^\/zh-CN/, '')}"`), route);
    assert.ok(html.includes(`hreflang="en" href="https://hololab.xenotech.studio${route.replace(/^\/zh-CN/, '')}"`), route);
    assert.ok(html.includes(`property="og:locale" content="${locale === 'en' ? 'en_US' : 'zh_CN'}"`), route);
  }
}
const en = JSON.parse(await read('src/i18n/en.json')), zh = JSON.parse(await read('src/i18n/zh.json'));
assert.deepEqual(Object.keys(en).sort(), Object.keys(zh).sort());
for (const d of docs.filter(d => d.locale === 'en' && d.source.startsWith('website/content/en/use/'))) assert.doesNotMatch(d.html, /[\u4e00-\u9fff]/);
console.log('Static heads (including legacy directory URLs) verified before JavaScript; UI dictionary keys match; English usage text and diagrams have no untranslated Chinese.');
