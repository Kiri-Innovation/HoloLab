import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import content from './content/docs/index.json';
import './styles/site.css';

type Theme = 'light' | 'dark' | 'system';
const sections = content.sections;
const docs = content.docs;
const pathname = window.location.pathname.replace(/\/$/, '') || '/';
const current = docs.find(doc => doc.href === pathname);
const section = sections.find(s => s.href === pathname || s.id === current?.section);
const isHome = pathname === '/';
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
  return <label className="theme-control"><span aria-hidden="true">◐</span><select aria-label="主题" value={theme} onChange={e => setTheme(e.target.value as Theme)}><option value="system">跟随系统</option><option value="light">浅色</option><option value="dark">深色</option></select></label>;
}
function Mark() { return <span className="mark" aria-hidden="true"><i /><i /><i /></span>; }
function Header() {
  return <header className="header"><div className="header-inner"><a className="brand" href="/" aria-label="HoloLab 首页"><Mark />HoloLab</a><nav aria-label="主导航"><a href="/" aria-current={isHome ? 'page' : undefined}>概览</a>{sections.map(s => <a key={s.id} href={s.href} aria-current={section?.id === s.id ? 'page' : undefined}>{s.title}</a>)}</nav><ThemeControl /></div></header>;
}
function Pipeline() {
  return <figure className="pipeline"><div className="figure-bar"><span><span className="tiny-square" /> 工作流</span><span>数据 → 重建 → 训练 → 评估</span></div><div className="graph" role="img" aria-label="工作流示意：多视角图像依次连接相机重建、3DGS 训练和渲染评估。"><svg className="wires" viewBox="0 0 1000 240" preserveAspectRatio="none" aria-hidden="true"><path d="M225 120H295M465 120H535M705 120H775" /></svg>{[{ name: '多视角图像', kind: '数据', rows: ['图像集合', '相机视角'] }, { name: '相机重建', kind: 'SfM', rows: ['相机位姿', '稀疏点云'] }, { name: '3DGS 训练', kind: '训练', rows: ['迭代参数', '模型输出'] }, { name: '渲染与评估', kind: '结果', rows: ['新视角图像', '评估指标'] }].map((n,i) => <div className="graph-node" key={n.name}><div className="node-top"><span className="node-index">0{i+1}</span>{n.kind}</div><strong>{n.name}</strong><div className="node-rows">{n.rows.map(row => <span key={row}>{row}</span>)}</div><span className="port port-in"/><span className="port port-out"/></div>)}</div><figcaption>流程示意 · 实际节点与预览取决于已安装的 Pack。</figcaption></figure>;
}
function Home() {
  return <main id="main" className="home"><section className="hero"><div className="eyebrow"><span className="tiny-square" /> HoloLab / 视觉研究工作流</div><h1>把你手上的视觉算法，<br className="desktop-break"/>接成能跑的工作流</h1><p className="intro">HoloLab 是一个可视化工作流工具，用来组织计算机视觉实验：<br className="desktop-break"/>在画布上把数据、重建、训练、评估串起来，配置每一步的参数，<br className="desktop-break"/>看着它跑完，然后检查结果。</p><p className="execution-note">算法通过 Pack 接入，各自在独立进程和你配置的环境里执行。</p><div className="actions"><a className="button primary" href="/docs/quick-start">快速开始 <span>↗</span></a><a className="button" href="/docs">阅读文档 <span>→</span></a></div></section><Pipeline /><section className="capabilities"><div><div className="eyebrow">在画布上工作</div><h2>你能拿它做什么</h2></div><ul><li><strong>接上自己的算法</strong><p>写一个 manifest 描述输入、输出、参数和执行命令，就能在画布上用它搭流程。</p></li><li><strong>串起一条链</strong><p>端口之间有类型检查，接错了当场告诉你哪里不对。</p></li><li><strong>跑起来看得见</strong><p>每一步的进度、日志、状态都能看到，随时可以停。</p><small>进度信息取决于 Pack 的实现。</small></li><li><strong>结果能追</strong><p>每次运行留下记录，产物可以预览，也能跳回文件所在位置。</p><small>跳转文件位置需要 Flops Cobrowser 集成。</small></li></ul></section><section className="audience"><div><div className="eyebrow">研究 / 开发</div><h2>给谁用</h2></div><p>做 SfM、NeRF、3D Gaussian Splatting 这类研究的人，以及想把已有算法接进可视化流程的开发者。</p></section><section className="start"><div><h2>开始</h2><p>按快速开始装好、跑起来，然后添加节点、连线、选设备、运行。</p></div><a className="button primary" href="/docs/quick-start">快速开始 →</a></section></main>;
}
function DocNav() {
  return <nav className="doc-nav" aria-label="文档导航">{sections.map(s => { const entries = docs.filter(d => d.section === s.id); const groups = [...new Set(entries.map(d => d.group))]; return <div className="nav-section" key={s.id}><a className="section-link" aria-current={pathname === s.href ? 'page' : undefined} href={s.href}>{s.title}<span>↗</span></a>{groups.map(group => <div key={group}><div className="nav-group">{group}</div>{entries.filter(d => d.group === group).map(d => <a className="doc-link" key={d.slug} aria-current={current?.slug === d.slug ? 'page' : undefined} href={d.href}>{d.title}</a>)}</div>)}</div>; })}</nav>;
}
const introductions: Record<string,string> = { use: '从安装开始，在画布上跑通第一个工作流，再查看结果。', build: '从编写 Pack 开始，了解算法如何接入，以及工作流和类型系统如何运行。', reference: '查阅 Pack、工作流格式，以及产物与工作空间的详细规则。' };
function DocsIndex() {
  const entries = docs.filter(d => d.section === section?.id);
  return <><div className="eyebrow">HoloLab 文档</div><h1>{section?.title === '使用' ? '开始使用 HoloLab' : section?.title === '开发' ? '开发文档' : '参考文档'}</h1><p className="page-intro">{introductions[section?.id ?? 'use']}</p><div className="doc-cards">{entries.map((d,i) => <a key={d.slug} href={d.href}><span className="doc-number">0{i+1}</span><div><strong>{d.title}</strong><small>{d.group}</small></div><span>→</span></a>)}</div>{section?.id === 'build' && entries.length === 0 && <div className="empty-docs"><h2>先了解工作流怎么运行</h2><p>准备接入算法前，可以先熟悉画布、参数和设备的使用方式。</p><a href="/docs/use/workflows">阅读「第一个工作流」 →</a></div>}</>;
}
function Documentation() {
  const siblings = docs.filter(d => d.section === current?.section);
  const index = siblings.findIndex(d => d.slug === current?.slug);
  return <div className="docs-layout"><aside className="sidebar"><div className="sidebar-title">文档 <span>HoloLab</span></div><DocNav /></aside><main id="main" className="document"><details className="mobile-nav"><summary>浏览文档</summary><DocNav /></details>{current ? <><div className="breadcrumb"><a href={section?.href}>{section?.title}</a><span>/</span>{current.group}</div>{current.slug === 'reference/artifacts' && <p className="reference-note">英文参考 · “Future direction” 部分为未实现的计划。</p>}<article className="prose" dangerouslySetInnerHTML={{ __html: current.html }} /><nav className="page-turn" aria-label="上一篇和下一篇">{siblings[index-1] ? <a href={siblings[index-1].href}><small>上一篇</small>← {siblings[index-1].title}</a> : <span/>}{siblings[index+1] && <a href={siblings[index+1].href}><small>下一篇</small>{siblings[index+1].title} →</a>}</nav></> : <DocsIndex />}</main><aside className="toc" aria-label="本页目录">{current && <><span>本页内容</span>{current.headings.filter(h => h.depth === 2).map(h => <a key={h.id} href={`#${h.id}`}>{h.title}</a>)}</>}</aside></div>;
}
function App() {
  useEffect(() => {
    document.title = `${current?.title ?? (isHome ? '视觉算法工作流' : section ? `${section.title}文档` : '页面不存在')} — HoloLab`;
    document.querySelector('link[rel="canonical"]')?.setAttribute('href', `https://hololab.xenotech.studio${pathname}`);
    if (current) document.querySelector('meta[name="description"]')?.setAttribute('content', `${current.title} · HoloLab ${section?.title}文档`);
    if (window.location.hash) { try { document.getElementById(decodeURIComponent(window.location.hash.slice(1)))?.scrollIntoView(); } catch { /* An invalid user-entered fragment is harmless. */ } }
  }, []);
  return <><a className="skip" href="#main">跳到正文</a><Header />{isHome ? <Home /> : isFound ? <Documentation /> : <main id="main" className="not-found"><div className="eyebrow">404</div><h1>没有找到这一页</h1><p>链接可能已变更，可以从文档目录继续查找。</p><a className="button primary" href="/docs">返回文档 →</a></main>}<footer className="footer"><a className="brand" href="/"><Mark />HoloLab</a><span>视觉算法，连接成工作流。</span><a href="/docs">文档 ↗</a></footer></>;
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);
