import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import content from './content/docs/index.json';
import './styles/site.css';
import './styles/i18n.css';
import './styles/doc-areas.css';
import './styles/screenshots.css';
import './browser-environment';
import { attachCodeCopy } from './code-copy';
import './styles/code-copy.css';
import './styles/browser-environment.css';
import { locale, localeKey, localize, t, label } from './i18n';

type Theme = 'light' | 'dark' | 'system';
const sections = content.sections.map(s => ({ ...s, href: localize(s.href), entryHref: content.docs.find(d => d.locale === locale && d.section === s.id)!.href, title: t(s.id as 'learn' | 'contributor') }));
const docs = content.docs.filter(d => d.locale === locale).map(d => ({ ...d, title: label(d.title), group: label(d.group) }));
const requestedPath = window.location.pathname.replace(/\/$/, '') || '/';
const directory = sections.find(s => s.href === requestedPath || s.aliases.some(href => localize(href) === requestedPath));
// Old directory links open the first article without adding a history entry.
if (directory) history.replaceState(null, '', directory.entryHref + location.search + location.hash);
const pathname = window.location.pathname.replace(/\/$/, '') || '/';
const current = docs.find(doc => doc.href === pathname);
const section = sections.find(s => s.href === pathname || s.aliases.some(href => localize(href) === pathname) || s.id === current?.section);
const isHome = pathname === '/' || pathname === '/zh-CN';
const isFound = isHome || !!current || !!section;

function ThemeControl() {
  const [theme, setTheme] = useState<Theme>(() => { try { const t = localStorage.getItem('hololab.website.theme'); return t === 'dark' || t === 'light' ? t : 'system'; } catch { return 'system'; } });
  useEffect(() => {
    const media = matchMedia('(prefers-color-scheme: dark)');
    const apply = () => { document.documentElement.dataset.theme = theme === 'system' ? media.matches ? 'dark' : 'light' : theme; };
    apply(); media.addEventListener('change', apply);
    try { localStorage.setItem('hololab.website.theme', theme); } catch { /* Private browsing still supports the current theme. */ }
    return () => media.removeEventListener('change', apply);
  }, [theme]);
  return <select className="theme-control" aria-label={t('theme')} value={theme} onChange={e => setTheme(e.target.value as Theme)}><option value="system">{t('system')}</option><option value="light">{t('light')}</option><option value="dark">{t('dark')}</option></select>;
}
function LanguageControl() {
  return <select className="language-control" aria-label={t('language')} value={locale} onChange={event => {
    const next = event.target.value as 'en' | 'zh-CN';
    try { localStorage.setItem(localeKey, next); } catch { /* URL remains the source of truth. */ }
    const counterpart = content.docs.find(d => d.slug === current?.slug && d.locale === next);
    let hash = location.hash;
    if (current && counterpart && hash) {
      const heading = current.headings.findIndex(h => `#${encodeURIComponent(h.id)}` === hash || `#${h.id}` === hash);
      hash = heading >= 0 && counterpart.headings[heading] ? `#${encodeURIComponent(counterpart.headings[heading].id)}` : '';
    }
    location.assign(localize(isHome ? '/' : pathname, next) + location.search + hash);
  }}><option value="en">English</option><option value="zh-CN">简体中文</option></select>;
}
function Header() {
  return <header className="header"><div className="header-inner"><a className="brand" href={localize('/')} aria-label={t('homeLabel')}><img src="/hololab-icon.svg" width="20" height="20" alt="" aria-hidden="true" style={{ verticalAlign: "middle", marginRight: 6, flexShrink: 0 }} />HoloLab</a><nav aria-label={t('nav')}><a href={localize('/')} aria-current={isHome ? 'page' : undefined}>{t('overview')}</a><a href={localize('/docs')} aria-current={section ? 'page' : undefined}>{t('docs')}</a></nav><LanguageControl /><ThemeControl /></div></header>;
}
function Pipeline() {
  const [theme, setTheme] = useState(() => document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light');
  useEffect(() => {
    const sync = () => setTheme(document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light');
    const observer = new MutationObserver(sync);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    sync();
    return () => observer.disconnect();
  }, []);
  const src = `/images/workflow-${theme}.webp`;
  return <figure className="pipeline"><div className="canvas-screenshot"><img src={src} width="2836" height="1038" decoding="async" alt={t('canvasAlt')} /></div><figcaption><span>{t('canvasCaption')}<small>{t('canvasNote')}</small></span></figcaption></figure>;
}
function Home() {
  return <main id="main" className="home"><section className="hero"><div className="eyebrow"><span className="tiny-square" /> HoloLab / {t('eyebrow')}</div><h1>{t('heroStart')}{locale === 'en' ? ' ' : ''}<br className="desktop-break"/>{t('heroEnd')}</h1><p className="intro">{t('intro')}</p><p className="execution-note">{t('execution')}</p><div className="actions"><a className="button primary" href={localize('/docs/quick-start')}>{t('install')} <span>↗</span></a><a className="button" href={localize('/docs')}>{t('readDocs')} <span>→</span></a></div></section><Pipeline /><section className="capabilities"><div><div className="eyebrow">{t('onCanvas')}</div><h2>{t('capabilities')}</h2></div><ul><li><strong>{t('connectTitle')}</strong><p>{t('connectBody')}</p></li><li><strong>{t('typesTitle')}</strong><p>{t('typesBody')}</p></li><li><strong>{t('statusTitle')}</strong><p>{t('statusBody')}</p><small>{t('statusNote')}</small></li><li><strong>{t('resultsTitle')}</strong><p>{t('resultsBody')}</p><small>{t('resultsNote')}</small></li></ul></section><section className="audience"><div><div className="eyebrow">{t('audienceLabel')}</div><h2>{t('audienceTitle')}</h2></div><p>{t('audienceBody')}</p></section><section className="start"><div><h2>{t('start')}</h2><p>{t('startBody')}</p></div><a className="button primary" href={localize('/docs/quick-start')}>{t('install')} →</a></section></main>;
}
function DocNav() {
  return <nav className="doc-nav" aria-label={t('docNav')}>{sections.filter(s => s.id === section?.id).map(s => {
    const entries = docs.filter(d => d.section === s.id);
    const groups = [...new Set(entries.map(d => d.group))];
    return <section className="nav-section" key={s.id} aria-label={s.title}>
      {groups.map(group => <section className="nav-group" key={group} aria-label={group}>
        <h2>{group}</h2>
        <ul>{entries.filter(d => d.group === group).map(d => <li key={d.slug}><a className="doc-link" aria-current={current?.slug === d.slug ? 'page' : undefined} href={d.href}>{d.title}</a></li>)}</ul>
      </section>)}
    </section>;
  })}</nav>;
}
function DocAreas() {
  return <nav className="doc-areas" aria-label={t('docAreas')}><div>{sections.map(s => <a key={s.id} href={s.entryHref} aria-current={section?.id === s.id ? 'page' : undefined}>{s.title}</a>)}</div></nav>;
}
function Footer() {
  return <footer className="footer"><a className="brand" href={localize('/')}>HoloLab</a><span>{t('footer')}</span><a href={localize('/docs')}>{t('docs')} ↗</a></footer>;
}
function attachDocTabs() {
  const cleanups: (() => void)[] = [];
  document.querySelectorAll<HTMLElement>('.doc-tabs[data-tabs]').forEach(tabs => {
    const panels = [...tabs.querySelectorAll<HTMLElement>('[data-tab-panel]')];
    const buttons = [...tabs.querySelectorAll<HTMLButtonElement>('[data-tab]')];
    const panelByName = new Map(panels.map(panel => [panel.dataset.tabPanel, panel]));
    if (panels.length !== buttons.length || !buttons.every(button => panelByName.has(button.dataset.tab))) return;

    // Markdown owns the tab bodies. Markers route each following block into
    // the right panel until the next heading belonging to the document.
    let active = buttons[0].dataset.tab!;
    let node = tabs.nextElementSibling;
    while (node && node.id !== tabs.dataset.tabsEnd) {
      const next = node.nextElementSibling;
      const marker = node as HTMLElement;
      if (marker.matches('[data-tab-source]') && panelByName.has(marker.dataset.tabSource)) {
        active = marker.dataset.tabSource!;
        node.remove();
      } else {
        panelByName.get(active)?.append(node);
      }
      node = next;
    }

    const select = (name: string, focus = false) => {
      buttons.forEach(button => {
        const selected = button.dataset.tab === name;
        button.setAttribute('aria-selected', String(selected));
        button.tabIndex = selected ? 0 : -1;
        if (selected && focus) button.focus();
      });
      panels.forEach(panel => { panel.hidden = panel.dataset.tabPanel !== name; });
    };
    const revealFragment = () => {
      if (!location.hash) return;
      try {
        const target = document.getElementById(decodeURIComponent(location.hash.slice(1)));
        const tab = target?.matches('[data-tab]') ? target : target?.querySelector<HTMLElement>('[data-tab]');
        if (tab?.dataset.tab && tab.closest('.doc-tabs[data-tabs]') === tabs) { select(tab.dataset.tab); return; }
        const panel = panels.find(candidate => candidate.contains(target));
        if (panel) select(panel.dataset.tabPanel!);
      } catch { /* Invalid fragments are harmless. */ }
    };
    buttons.forEach((button, index) => {
      const onClick = () => select(button.dataset.tab!);
      const onKeyDown = (event: KeyboardEvent) => {
        const keys = ['ArrowLeft', 'ArrowRight', 'Home', 'End'];
        if (!keys.includes(event.key)) return;
        event.preventDefault();
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length;
        select(buttons[next].dataset.tab!, true);
      };
      button.addEventListener('click', onClick);
      button.addEventListener('keydown', onKeyDown);
      cleanups.push(() => { button.removeEventListener('click', onClick); button.removeEventListener('keydown', onKeyDown); });
    });
    window.addEventListener('hashchange', revealFragment);
    cleanups.push(() => window.removeEventListener('hashchange', revealFragment));
    revealFragment();
  });
  return () => cleanups.forEach(cleanup => cleanup());
}
function Documentation() {
  useEffect(() => attachCodeCopy({ copy: t('copyCode'), copied: t('copiedCode'), failed: t('copyCodeFailed') }), []);
  useEffect(() => attachDocTabs(), []);
  useEffect(() => {
    // Follow the site's resolved theme, including a manual override of the OS theme.
    const syncPictures = () => {
      const dark = document.documentElement.dataset.theme === 'dark';
      document.querySelectorAll<HTMLSourceElement>('.doc-screenshot picture source').forEach(source => {
        source.media = dark ? '(min-width: 0px)' : 'not all';
      });
    };
    syncPictures();
    const observer = new MutationObserver(syncPictures);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => observer.disconnect();
  }, []);
  const siblings = docs.filter(d => d.section === current?.section);
  const index = siblings.findIndex(d => d.slug === current?.slug);
  return <><DocAreas /><div className="docs-layout"><aside className="sidebar"><DocNav /></aside><main id="main" className="document"><details className="mobile-nav"><summary>{t('browse')}</summary><DocNav /></details>{current ? <><div className="breadcrumb"><a href={section?.entryHref}>{section?.title}</a><span>/</span>{current.group}</div>{current.contentLanguage !== locale && <p className="reference-note">{t('englishNotice')}</p>}{current.slug === 'reference/artifacts' && <p className="reference-note">{t('futureNotice')}</p>}<article lang={current.contentLanguage} className="prose" dangerouslySetInnerHTML={{ __html: current.html }} /><nav className="page-turn" aria-label={t('pageTurn')}>{siblings[index-1] ? <a href={siblings[index-1].href}><small>{t('previous')}</small>← {siblings[index-1].title}</a> : <span/>}{siblings[index+1] && <a href={siblings[index+1].href}><small>{t('next')}</small>{siblings[index+1].title} →</a>}</nav></> : null}</main><aside className="toc" aria-label={t('tocLabel')}>{current && <><span>{t('toc')}</span>{current.headings.filter(h => h.depth === 2).map(h => <a key={h.id} href={`#${h.id}`}>{h.title}</a>)}</>}</aside><div className="docs-footer"><Footer /></div></div></>;
}
function App() {
  useEffect(() => {
    const canonicalPath = isHome ? localize('/') : pathname;
    const title = `${current?.title ?? (isHome ? t('homeTitle') : section?.title ?? t('notFound'))} — HoloLab`;
    document.title = title;
    document.documentElement.lang = locale;
    document.querySelector('link[rel="canonical"]')?.setAttribute('href', `https://hololab.xenotech.studio${canonicalPath}`);
    document.querySelector('meta[name="description"]')?.setAttribute('content', current ? `${current.title} · HoloLab` : t('intro'));
    document.querySelector('meta[property="og:locale"]')?.setAttribute('content', locale === 'en' ? 'en_US' : 'zh_CN');
    for (const language of ['en', 'zh-CN', 'x-default'] as const) {
      document.querySelector(`link[hreflang="${language}"]`)?.setAttribute('href', `https://hololab.xenotech.studio${localize(canonicalPath, language === 'zh-CN' ? 'zh-CN' : 'en')}`);
    }
    if (window.location.hash) { try { document.getElementById(decodeURIComponent(window.location.hash.slice(1)))?.scrollIntoView(); } catch { /* An invalid user-entered fragment is harmless. */ } }
  }, []);
  return <><a className="skip" href="#main">{t('skip')}</a><Header />{isHome ? <Home /> : isFound ? <Documentation /> : <main id="main" className="not-found"><div className="eyebrow">404</div><h1>{t('notFound')}</h1><p>{t('notFoundBody')}</p><a className="button primary" href={localize('/docs')}>{t('backDocs')} →</a></main>}{(isHome || !isFound) && <Footer />}</>;
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);
