import {useEffect, useRef, useState} from 'react';
import type {CatalogPack, ComputeNode} from '../wire';
import {canonicalPackageName} from '../packageIdentity';
import {EvidenceValue} from '../canvas/EvidenceValue';

type Release = {name:string; version:string; yanked:boolean; code_sha256:string; published_at:string; publisher:string; requires:unknown; env:string};
class PublicPackError extends Error {}
const readableError = (error:unknown) => error instanceof PublicPackError ? error.message : '请求未完成，请检查网络与节点连接后重试。';
async function request(url:string, init?:RequestInit) {
  const response = await fetch(url, init);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new PublicPackError(typeof data?.detail?.message === 'string' ? data.detail.message : '请求未完成，请检查网络与节点连接后重试。');
  return data;
}
export function CommunityPacks({catalog, nodes, onInstalled}: {catalog:CatalogPack[]; nodes:ComputeNode[]; onInstalled:()=>void}) {
  const [source,setSource]=useState('https://packs.xenotech.studio/');
  const [loaded,setLoaded]=useState('');
  const [names,setNames]=useState<string[]>([]);
  const [projects,setProjects]=useState<Record<string, {releases?:Release[]; error?:string}>>({});
  const [search,setSearch]=useState('');
  const [filtersOpen,setFiltersOpen]=useState(false);
  const [selected,setSelected]=useState('');
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [node,setNode]=useState('');
  const [installing,setInstalling]=useState('');
  const [results,setResults]=useState<Record<string,string>>({});
  const [installed,setInstalled]=useState<Set<string>>(new Set());
  const sequence=useRef(0);
  const target=node || nodes[0]?.node_id || '';
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
            if(id===sequence.current)setProjects(p=>({...p,[name]:{releases:Object.values(meta.releases)}}));
          } catch(e) {if(id===sequence.current)setProjects(p=>({...p,[name]:{error:readableError(e)}}));}
        }
      }));
    } catch(e) {if(id===sequence.current)setError(readableError(e));}
    finally {if(id===sequence.current)setBusy(false);}
  }
  useEffect(()=>{void load();return ()=>{sequence.current++;};},[]);
  const releases=projects[selected]?.releases ?? [];
  const isInstalled=(release:Release)=>installed.has(`${target}:${release.name}@${release.version}`) || catalog.some(p=>canonicalPackageName(p.name)===release.name && p.version===release.version && p.node_ids.includes(target));
  async function install(release:Release) {
    const spec=`${release.name}@${release.version}`, key=`${target}:${spec}`, destinationNode=target;
    setInstalling(key);setResults(r=>({...r,[key]:'正在下载、校验并安装…'}));
    try {
      const result=await request('/api/packs/install',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({spec,source:loaded,node_id:destinationNode,expected:release.code_sha256})});
      setInstalled(previous=>new Set([...previous,key]));
      setResults(r=>({...r,[key]:`安装成功 · ${result.destination || '已在目标节点发现'}。环境依赖需单独准备。`}));
      onInstalled();
    } catch(e) {setResults(r=>({...r,[key]:`安装失败：${readableError(e)}`}));}
    finally {setInstalling('');}
  }
  return <section aria-label="社区包" className="hl-pack-workspace">
    <aside className="hl-dock hl-pack-sidebar">
    <div className="hl-dock-heading"><h2 className="hl-section-title">社区</h2></div>
    <button className="hl-pack-directory-toggle" aria-expanded={filtersOpen} onClick={()=>setFiltersOpen(!filtersOpen)}>注册表与筛选 {filtersOpen ? '▴' : '▾'}</button>
    <div className="hl-pack-directory" data-open={filtersOpen}>
    <div className="hl-pack-registry"><form onSubmit={e=>{e.preventDefault();void load();}}>
      <input aria-label="注册表目录 URL" type="url" required value={source} onChange={e=>setSource(e.target.value)} disabled={!!installing}/>
      <button className="hl-evidence-primary" disabled={busy || !!installing}>{busy?'正在读取…':'加载注册表'}</button>
    </form><p>输入注册表根地址。网关读取目录，选中的计算节点下载并安装；不会自动安装环境依赖。</p>
    <label>安装到计算节点 <select aria-label="安装到计算节点" value={target} disabled={!!installing} onChange={e=>setNode(e.target.value)}><option value="">选择计算节点</option>{nodes.map(n=><option key={n.node_id} value={n.node_id}>{n.node_name || n.node_id}</option>)}</select></label>
    <p>安装目录：{nodes.find(n=>n.node_id===target)?.pack_dirs?.join('、') || '选择计算节点后读取配置目录'}。使用第一个包集合目录，跳过单包目录。</p></div>
    <label className="hl-pack-search">搜索社区包<input type="search" aria-label="搜索社区包" value={search} onChange={e=>setSearch(e.target.value)} placeholder="名称或 scope" /></label>
    <p className="hl-pack-boundary">{names.length} 个包 · 发布版本与环境要求</p>
    </div></aside>
    <div className="hl-pack-detail">
    {error && <p role="alert" className="hl-pack-notice">{error}</p>}
    {busy && <p role="status">正在读取{selected || '社区目录'}…</p>}
    {!loaded && !busy && !error && <div className="hl-pack-empty">加载注册表，浏览社区算法包。</div>}
    {loaded && !busy && !names.length && <div className="hl-pack-empty">这个源尚未发布包。</div>}
    {selected ? <>
      <button disabled={!!installing} onClick={()=>setSelected('')}>← 全部社区包</button>
      <h2>{selected}</h2>
      {projects[selected]?.error && <p role="alert">{projects[selected].error} 请重新加载注册表。</p>}
      {!projects[selected] && <p role="status">正在读取版本…</p>}
      {projects[selected]?.releases?.length === 0 && <p>这个包没有已发布版本。</p>}
      {releases.map(release=>{
        const spec=`${release.name}@${release.version}`,key=`${target}:${spec}`;
        const local=isInstalled(release);
        return <article className="hl-pack-card" key={spec} data-pack-key={spec}><header><h3>{release.name}</h3><span>{release.version}</span></header>
          <p>{!target?'选择目标节点以查看安装状态':local?'已安装（目标节点同名同版本）':'未安装'} · {release.yanked?'已 yank · 禁止新安装':'可安装'}</p>
          <dl className="hl-pack-facts"><div><dt>发布时间</dt><dd>{release.published_at}</dd></div><div><dt>发布者</dt><dd>{release.publisher}</dd></div><div><dt>来源</dt><dd>{loaded}</dd></div></dl>
          <p>代码身份 · SHA-256</p><EvidenceValue value={release.code_sha256}/>
          <p>安装后环境 · {release.env || '未指定运行环境'}</p>
          {release.requires == null ? <p>未声明 requires，不代表缺失环境，也不保证一定能运行。</p>:<EvidenceValue value={release.requires}/>}
          <button className="hl-evidence-primary" disabled={release.yanked || local || !target || !!installing} onClick={()=>void install(release)}>{installing===key?'安装中…':local?'已安装':release.yanked?'不可安装（yanked）':'安装'}</button>
          {results[key] && <p role="status">{results[key]}</p>}
        </article>;
      })}</> : <>
      <div className="hl-pack-heading"><h1>社区算法包</h1><span>{names.filter(name=>name.toLowerCase().includes(search.toLowerCase())).length} 个包</span></div>
      <div className="hl-pack-grid">{names.filter(name=>name.toLowerCase().includes(search.toLowerCase())).map(name=>{
        const project=projects[name], versions=project?.releases;
        const latest=versions?.slice().sort((a,b)=>b.published_at.localeCompare(a.published_at))[0];
        return <article className="hl-pack-card" key={name}>
          <h3><button className="hl-pack-project-link" onClick={()=>setSelected(name)}>{name}</button></h3>
          {!project ? <p role="status">正在读取版本…</p> : project.error ? <p role="alert">{project.error}</p> : <>
            <p>{versions?.length} 个版本 · {latest?.version ?? '暂无发布版本'}</p>
            {latest && <><p>{versions?.every(r=>r.yanked) ? '全部已 yank · 不可安装' : latest.yanked ? '最新版本已 yank · 可查看其他版本' : '最新版本可安装'} · {!target ? '未选择目标节点' : versions?.some(isInstalled) ? '含已安装版本' : '未安装'}</p>
            <dl><dt>最近发布</dt><dd>{latest.published_at}</dd><dt>发布者</dt><dd>{latest.publisher}</dd></dl>
            <p>最新版本环境 · {latest.env || '未指定运行环境'}</p>
            {latest.requires == null ? <p>未声明 requires，不等于环境缺失。</p> : <EvidenceValue value={latest.requires}/>}</>}
          </>}
          <button onClick={()=>setSelected(name)}>查看版本与详情 →</button>
        </article>;
      })}</div>
      {loaded && names.length>0 && !names.some(name=>name.toLowerCase().includes(search.toLowerCase())) && <p className="hl-pack-empty">没有符合搜索条件的包。</p>}
      </>}
    </div>
  </section>;
}
