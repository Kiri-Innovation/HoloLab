import {packDisplayNames} from './packDisplayName';
import {packManagerRoute, packManagerPath} from './packManagerRoute';
import {CategoryIndex} from './CategoryIndex';
import {browseProjects, latestRelease, projectCategory, publishedTime, type CommunityRelease as Release} from './communityBrowserModel';
import {useEffect, useMemo, useRef, useState} from 'react';
import type {CatalogPack, ComputeNode} from '../wire';
import {canonicalPackageName} from '../packageIdentity';
import {EvidenceValue} from '../canvas/EvidenceValue';

class PublicPackError extends Error {}
const readableError = (error:unknown) => error instanceof PublicPackError ? error.message : '请求未完成，请检查网络与节点连接后重试。';
async function request(url:string, init?:RequestInit) {
  const response = await fetch(url, init);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new PublicPackError(typeof data?.detail?.message === 'string' ? data.detail.message : '请求未完成，请检查网络与节点连接后重试。');
  return data;
}
export function CommunityPacks({active, catalog, nodes, onInstalled, onNavigate}: {onNavigate:(path:string)=>void; active:boolean; catalog:CatalogPack[]; nodes:ComputeNode[]; onInstalled:()=>void}) {
  const [source,setSource]=useState('https://packs.xenotech.studio');
  const [sourceDraft,setSourceDraft]=useState('https://packs.xenotech.studio');
  const [loaded,setLoaded]=useState('');
  const [names,setNames]=useState<string[]>([]);
  const displayName=useMemo(()=>packDisplayNames(names),[names]);
  const [projects,setProjects]=useState<Record<string, {releases?:Release[]; error?:string}>>({});
  const [search,setSearch]=useState('');
  const {communitySection:index,category,publisher,scope}=packManagerRoute(window.location.search);
  const navigate=(section:string,extra:Record<string,string>={})=>onNavigate(packManagerPath('community',section,extra));
  const installedFilter=index==='installed'?'installed':index==='uninstalled'?'uninstalled':'all';
  useEffect(()=>{if(active)setSearch('');},[active]);
  const dialog=useRef<HTMLDialogElement>(null);
  const [filtersOpen,setFiltersOpen]=useState(false);
  const [selected,setSelected]=useState('');
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [node,setNode]=useState('');
  const [installing,setInstalling]=useState('');
  const [results,setResults]=useState<Record<string,string>>({});
  const [installed,setInstalled]=useState<Set<string>>(new Set());
  const sequence=useRef(0);
  const target=nodes.some(n=>n.node_id===node) ? node : nodes[0]?.node_id || '';
  const machine=nodes.find(n=>n.node_id===target)?.node_name || target;
  async function load() {
    const id=++sequence.current;
    setBusy(true); setError(''); setProjects({}); setSelected(''); setNames([]); setLoaded('');
    try {
      const origin=source;
      const data=await request(`/api/packs/registry?${new URLSearchParams({source:origin})}`);
      if(id!==sequence.current)return;
      const list:string[]=data.projects.map((p:{name:string})=>p.name);
      setNames(list);setLoaded(origin);
      let cursor=0;
      await Promise.all(Array.from({length:Math.min(4,list.length)},async()=>{
        while(cursor<list.length && id===sequence.current){
          const name=list[cursor++];
          try {
            const meta=await request(`/api/packs/registry?${new URLSearchParams({source:origin,name})}`);
            if(id===sequence.current)setProjects(p=>({...p,[name]:{releases:(Object.values(meta.releases) as Release[]).sort((a,b)=>publishedTime(b.published_at)-publishedTime(a.published_at))}}));
          } catch(e) {if(id===sequence.current)setProjects(p=>({...p,[name]:{error:readableError(e)}}));}
        }
      }));
    } catch(e) {if(id===sequence.current)setError(readableError(e));}
    finally {if(id===sequence.current)setBusy(false);}
  }
  // Preserve an in-flight installation when switching views; fetch on entering the community.
  useEffect(()=>{if(active && !installing) void load();},[active,source]);
  useEffect(()=>()=>{sequence.current++;},[]);
  const choose=(name:string)=>setSelected(name);
  useEffect(()=>{
    if(selected && active) {if(!dialog.current?.open) dialog.current?.showModal();}
    else dialog.current?.close();
  },[selected,active]);
  const applySource=()=>{try {const url=new URL(sourceDraft);if(['https:','http:'].includes(url.protocol))setSource(sourceDraft.trim());}catch{/* Native URL validation keeps the previous source active. */}};
  const releases=projects[selected]?.releases ?? [];
  const isInstalled=(release:Release)=>installed.has(`${target}:${release.name}@${release.version}`) || catalog.some(p=>canonicalPackageName(p.name)===release.name && p.version===release.version && p.node_ids.includes(target));
  const allProjects=names.map(name=>({name,...projects[name]}));
  const matching=browseProjects(allProjects,{search,category:index==='category'?category:'',publisher:index==='publisher'?publisher:'',scope:index==='scope'?scope:'',installed:installedFilter},isInstalled);
  const publishers=[...new Set(allProjects.map(project=>latestRelease(project)?.publisher).filter((value):value is string=>!!value))].sort();
  const scopes=[...new Set(names.map(name=>name.split('/')[0]))].sort();
  const changeIndex=(next:typeof index)=>{if(next==='home')setSearch('');setSelected('');navigate(next);};
  async function install(release:Release) {
    const spec=`${release.name}@${release.version}`, key=`${target}:${spec}`, destinationNode=target;
    setInstalling(key);setResults(r=>({...r,[key]:'正在下载、校验并安装…'}));
    try {
      await request('/api/packs/install',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({spec,source:loaded,node_id:destinationNode,expected:release.code_sha256})});
      setInstalled(previous=>new Set([...previous,key]));
      setResults(r=>({...r,[key]:'安装成功，已添加到本地安装。环境依赖需单独准备。'}));
      onInstalled();
    } catch(e) {setResults(r=>({...r,[key]:`安装失败：${readableError(e)}`}));}
    finally {setInstalling('');}
  }
  return <section aria-label="社区包" className="hl-pack-workspace">
    <aside className="hl-dock hl-pack-sidebar">
    <div className="hl-dock-heading"><h2 className="hl-section-title">社区</h2></div>
    <button className="hl-pack-directory-toggle" aria-expanded={filtersOpen} onClick={()=>setFiltersOpen(!filtersOpen)}>浏览索引 {filtersOpen ? '▴' : '▾'}</button>
    <div className="hl-pack-directory" data-open={filtersOpen}>
    <nav className="hl-community-index hl-pack-view-nav" aria-label="社区视图">
      {([['home','首页'],['recent','最近更新'],['category','功能分类'],['publisher','发布者'],['scope','Scope'],['installed','已安装'],['uninstalled','未安装']] as const).map(([value,label])=><button key={value} aria-current={index===value?'page':undefined} onClick={()=>changeIndex(value)}>{label}</button>)}
    </nav>
    {index==='category' && <><button onClick={()=>navigate('category')} aria-current={!category?'true':undefined}>全部分类</button><CategoryIndex items={allProjects.map(project=>({name:project.name,version:'',category:projectCategory(project)}))} selected={category} onSelect={value=>navigate('category',{category:value})}/></>}
    {index==='publisher' && <label className="hl-pack-search">发布者<select aria-label="筛选发布者" value={publisher} onChange={e=>navigate('publisher',{publisher:e.target.value})}><option value="">全部发布者</option>{publishers.map(value=><option key={value} value={value}>{value}</option>)}</select></label>}
    {index==='scope' && <label className="hl-pack-search">Scope<select aria-label="筛选 scope" value={scope} onChange={e=>navigate('scope',{scope:e.target.value})}><option value="">全部 scope</option>{scopes.map(value=><option key={value} value={value}>{value}</option>)}</select></label>}
    <div className="hl-community-installed"><small>{target ? `查看 ${machine} 上的安装状态` : '连接机器后可查看安装状态'}</small>
    {nodes.length>1 && <select aria-label="筛选机器" value={target} disabled={!!installing} onChange={e=>setNode(e.target.value)}>{nodes.map(n=><option key={n.node_id} value={n.node_id}>{n.node_name || n.node_id}</option>)}</select>}</div>
    <details className="hl-pack-source-settings"><summary>源设置</summary>
      <label>注册表地址<input aria-label="注册表地址" type="url" required value={sourceDraft} disabled={!!installing}
        onChange={e=>setSourceDraft(e.target.value)} onBlur={e=>{if(e.target.checkValidity())applySource();}}
        onKeyDown={e=>{if(e.key==='Enter'){e.preventDefault();e.currentTarget.reportValidity();e.currentTarget.blur();}}}/></label>
      <small>修改后离开输入框或按回车，自动更新目录。</small>
    </details>
    </div></aside>
    <div className="hl-pack-detail hl-community-browser">
    <label className="hl-community-search">搜索社区包<input type="search" aria-label="搜索社区包" value={search} onChange={e=>setSearch(e.target.value)} placeholder="搜索包名、功能分类或发布者…" /></label>
    <div className="hl-pack-heading"><h1>{index==='category' && category ? category : index==='publisher' && publisher ? publisher : index==='scope' && scope ? scope : index==='home'?'首页':index==='installed'?'已安装':index==='uninstalled'?'未安装':index==='category'?'功能分类':index==='publisher'?'发布者':index==='scope'?'Scope':'最近更新'}</h1><span>{matching.length} 个包 · 最近发布优先</span></div>
    {error && <div role="alert" className="hl-pack-notice">{error}<button onClick={()=>void load()} disabled={busy}>重试</button></div>}
    {busy && <div role="status" className="hl-pack-loading">正在读取社区包…<div className="hl-pack-grid">{[0,1,2].map(i=><div key={i} className="hl-pack-placeholder" aria-hidden="true" />)}</div></div>}

    {loaded && !busy && !names.length && <div className="hl-pack-empty">这个源尚未发布包。</div>}
    <dialog ref={dialog} className="hl-community-dialog" aria-labelledby="community-detail-title" onClose={()=>setSelected('')}>
      <header><h2 id="community-detail-title">{displayName(selected)}</h2><button autoFocus aria-label="关闭包详情" onClick={()=>dialog.current?.close()}>关闭</button></header>
      {projects[selected]?.error && <p role="alert">{projects[selected].error} <button onClick={()=>void load()}>重试</button></p>}
      {!projects[selected] && <p role="status">正在读取版本…</p>}
      {projects[selected]?.releases?.length === 0 && <p>这个包没有已发布版本。</p>}
      {nodes.length > 1 ? <label>安装到哪台机器 <select aria-label="安装到哪台机器" value={target} disabled={!!installing} onChange={e=>setNode(e.target.value)}>{nodes.map(n=><option key={n.node_id} value={n.node_id}>{n.node_name || n.node_id}</option>)}</select></label>
        : <p>{target ? `安装到 ${machine}` : '没有可用机器，请先连接一台机器。'}</p>}
      <p className="hl-pack-boundary">安装包不会自动配置运行环境。</p>
      {releases.map(release=>{
        const spec=`${release.name}@${release.version}`,key=`${target}:${spec}`;
        const local=isInstalled(release);
        return <article className="hl-pack-card" key={spec} data-pack-key={spec}><header><h3>{displayName(release.name)}</h3><span>{release.version}</span></header>
          <p>{!target?'等待可用机器':local?'已安装（所选机器）':'未安装'} · {release.yanked?'已 yank · 禁止新安装':'可安装'}</p>
          <dl className="hl-pack-facts"><div><dt>发布时间</dt><dd>{release.published_at}</dd></div><div><dt>发布者</dt><dd>{release.publisher}</dd></div><div><dt>来源</dt><dd>{loaded}</dd></div></dl>
          <p>代码身份 · SHA-256</p><EvidenceValue value={release.code_sha256}/>
          <p>安装后环境 · {release.env || '未指定运行环境'}</p>
          {release.requires == null ? <p>未声明 requires，不代表缺失环境，也不保证一定能运行。</p>:<EvidenceValue value={release.requires}/>}
          <button className="hl-evidence-primary" disabled={release.yanked || local || !target || !!installing} onClick={()=>void install(release)}>{installing===key?'安装中…':local?'已安装':release.yanked?'不可安装（yanked）':'安装'}</button>
          {results[key] && <p role="status">{results[key]}</p>}
        </article>;
      })}
    </dialog>
    {index==='home' && <h2>最近更新</h2>}
    {['installed','uninstalled'].includes(index) && !target && <p role="status">连接机器后才能确认安装状态。</p>}
    <div className="hl-pack-grid hl-community-grid">{matching.map(project=>{
      const latest=latestRelease(project), versions=project.releases;
      return <article key={project.name} data-project={project.name} data-published-at={latest?.published_at ?? ''} className="hl-pack-card">
        <button className="hl-community-card" onClick={()=>choose(project.name)} aria-label={`查看 ${displayName(project.name)} 详情`}>
          <strong>{displayName(project.name)}</strong>
          {!versions ? <span>{project.error ? '版本信息暂不可用' : '正在读取版本…'}</span> : <>
            <span className="hl-pack-version">{latest?.version ?? '暂无发布版本'}</span>
            {latest && <><time dateTime={latest.published_at}>{Number.isFinite(publishedTime(latest.published_at)) ? new Date(latest.published_at).toLocaleString() : '发布时间未知'}</time>
            <span>发布者 · {latest.publisher || '未提供'}</span>
            <span>分类 · {projectCategory(project).join(' / ')}</span>
            <span>{!target ? '等待可用机器' : versions.some(isInstalled) ? '已安装' : '未安装'}</span>
            <span>{versions.every(r=>r.yanked) ? '全部已 yank · 不可安装' : latest.yanked ? '最新版本已 yank · 查看其他版本' : '可安装'}</span></>}
          </>}
          <span className="hl-community-card-action">查看版本与详情 →</span>
        </button>
        {project.error && <p role="alert">{project.error} <button onClick={()=>void load()}>重试</button></p>}
      </article>;
    })}</div>
    {loaded && !busy && names.length>0 && !matching.length && <p className="hl-pack-empty">没有符合搜索或筛选条件的包。</p>}
    </div>
  </section>;
}
