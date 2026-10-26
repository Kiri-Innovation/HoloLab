import {useRef, useState} from 'react';
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
  const [releases,setReleases]=useState<Release[]>([]);
  const [selected,setSelected]=useState('');
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [node,setNode]=useState('');
  const [installing,setInstalling]=useState('');
  const [results,setResults]=useState<Record<string,string>>({});
  const [installed,setInstalled]=useState<Set<string>>(new Set());
  const sequence=useRef(0);
  const target=node || nodes[0]?.node_id || '';
  async function load(name?:string) {
    const id=++sequence.current;
    setBusy(true); setError(''); setReleases([]); setSelected(name || '');
    if (!name) {setNames([]);setLoaded('');}
    try {
      const origin=name ? loaded : source;
      const data=await request(`/api/packs/registry?${new URLSearchParams({source:origin,...(name?{name}:{})})}`);
      if(id!==sequence.current)return;
      if(name) setReleases(Object.values(data.releases));
      else {setNames(data.projects.map((p:{name:string})=>p.name));setLoaded(origin);}
    } catch(e) {if(id===sequence.current)setError(readableError(e));}
    finally {if(id===sequence.current)setBusy(false);}
  }
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
  return <section aria-label="社区包">
    <div className="hl-pack-registry"><form onSubmit={e=>{e.preventDefault();void load();}}>
      <input aria-label="注册表目录 URL" type="url" required value={source} onChange={e=>setSource(e.target.value)} disabled={!!installing}/>
      <button className="hl-evidence-primary" disabled={busy || !!installing}>{busy?'正在读取…':'加载注册表'}</button>
    </form><p>输入注册表根地址。网关读取目录，选中的计算节点下载并安装；不会自动安装环境依赖。</p>
    <label>安装到计算节点 <select aria-label="安装到计算节点" value={target} disabled={!!installing} onChange={e=>setNode(e.target.value)}><option value="">选择计算节点</option>{nodes.map(n=><option key={n.node_id} value={n.node_id}>{n.node_name || n.node_id}</option>)}</select></label>
    <p>安装目录：{nodes.find(n=>n.node_id===target)?.pack_dirs?.join('、') || '选择计算节点后读取配置目录'}。使用第一个包集合目录，跳过单包目录。</p></div>
    {error && <p role="alert" className="hl-pack-notice">{error}</p>}
    {busy && <p role="status">正在读取{selected || '社区目录'}…</p>}
    {!loaded && !busy && !error && <div className="hl-pack-empty">加载注册表，浏览社区算法包。</div>}
    {loaded && !busy && !names.length && <div className="hl-pack-empty">这个源尚未发布包。</div>}
    <div className="hl-pack-community">
      <nav aria-label="社区包列表">{names.map(name=><button key={name} aria-current={selected===name?'true':undefined} disabled={!!installing} onClick={()=>void load(name)}>{name}</button>)}</nav>
      <div>{loaded && names.length>0 && !selected && <p>选择一个包，查看版本与安装要求。</p>}
      {selected && !busy && !error && !releases.length && <p>这个包没有已发布版本。</p>}
      {releases.map(release=>{
        const spec=`${release.name}@${release.version}`,key=`${target}:${spec}`;
        const local=installed.has(key) || catalog.some(p=>canonicalPackageName(p.name)===release.name && p.version===release.version && p.node_ids.includes(target));
        return <article className="hl-pack-card" key={spec} data-pack-key={spec}><header><h3>{release.name}</h3><span>{release.version}</span></header>
          <p>{!target?'选择目标节点以查看安装状态':local?'已安装（目标节点同名同版本）':'未安装'} · {release.yanked?'已 yank · 禁止新安装':'可安装'}</p>
          <dl className="hl-pack-facts"><div><dt>发布时间</dt><dd>{release.published_at}</dd></div><div><dt>发布者</dt><dd>{release.publisher}</dd></div><div><dt>来源</dt><dd>{loaded}</dd></div></dl>
          <p>代码身份 · SHA-256</p><EvidenceValue value={release.code_sha256}/>
          <p>安装后环境 · {release.env || '未指定运行环境'}</p>
          {release.requires == null ? <p>未声明 requires，不代表缺失环境，也不保证一定能运行。</p>:<EvidenceValue value={release.requires}/>}
          <button className="hl-evidence-primary" disabled={release.yanked || local || !target || !!installing} onClick={()=>void install(release)}>{installing===key?'安装中…':local?'已安装':release.yanked?'不可安装（yanked）':'安装'}</button>
          {results[key] && <p role="status">{results[key]}</p>}
        </article>;
      })}</div>
    </div>
  </section>;
}
