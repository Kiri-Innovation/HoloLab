import {packDisplayNames} from './packDisplayName';
import {LocalOverviewPage, LocalResultsPage, localStatusPages} from './LocalPackPages';
import {packManagerRoute, packManagerPath} from './packManagerRoute';
import {localOverview, isUndeclared} from './localOverviewModel';
import { useResizableSlot } from '../canvas/useResizableLayout';
import { PackageTree } from './PackageTree';
import { CommunityPacks } from './CommunityPacks';
import { useEffect, useMemo, useState } from 'react';
import type { CatalogPack, ComputeNode, WorkflowGraph } from '../wire';
import { CONTROL_STYLE } from '../ui/controlStyles';
import { ThemeToggle } from '../theme/ThemeToggle';
import { EnvironmentEvidence, useCapabilityChecks } from '../canvas/EnvironmentChecks';
import { EvidenceValue } from '../canvas/EvidenceValue';
import { OpenSourceButton } from '../canvas/OpenSourceButton';
import { writeToClipboard } from '../canvas/referenceAction';
import {usePackUsage} from './usePackUsage';
import { buildPackEntries, installedOn, installCommand, packProblemReasons, packEnvironmentStatus, recipeIssueLabel,
  type InventoryPack, type PackEntry, type RegistryRelease } from './packManagerModel';
import './packManager.css';

async function read<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, {signal});
  if (!response.ok) throw new Error('request_failed');
  return response.json();
}
interface CentralStatus { code_sha256?: string; registry_status?: string; state?: string; source?: string; publication?: Record<string, unknown> }

export function PackManager({onBack, onOpenWorkflow, onNavigate}: {
  onNavigate: (path: string) => void; onBack: () => void; onOpenWorkflow: (id: string) => void;
}) {
  const [catalog, setCatalog] = useState<CatalogPack[]>([]);
  const [inventory, setInventory] = useState<InventoryPack[]>([]);
  const [nodes, setNodes] = useState<ComputeNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [revision, setRevision] = useState(0);
  const [nodeId, setNodeId] = useState('');
  const [search, setSearch] = useState('');
  const route=packManagerRoute(window.location.search);
  const {view, localSection:section, pack:selected, category}=route;
  const usage = usePackUsage(section==='category'?selected:'', revision, view==='local');
  const workflows = usage.items;
  const navigateLocal=(next:string,pack='',category='')=>{if(next==='overview')setSearch('');onNavigate(packManagerPath('local',next,{pack,category}));};
  useEffect(()=>{setSearch('');},[view]);
  const leftDock = useResizableSlot('hl-layout-left', 300, 180, 420);
  const registry = useMemo<RegistryRelease[]>(() => [], []);
  const [central, setCentral] = useState<Record<string, {data?: CentralStatus; busy?: boolean; failed?: boolean}>>({});
  const [commandStatus, setCommandStatus] = useState<Record<string, string>>({});

  useEffect(() => {
    const controller = new AbortController();
    const signal = controller.signal;
    setLoading(true); setWarnings([]); setCentral({});
    void (async () => {
      const priorityKey = section==='category' ? selected : '';
      const requests = [priorityKey ? `/api/pack-catalog?key=${encodeURIComponent(priorityKey)}` : '/api/pack-catalog', '/api/packs', '/api/nodes'];
      const results = await Promise.allSettled(requests.map(url => read<unknown[]>(url, signal).then(data => {
        if (!Array.isArray(data)) throw new Error('invalid_list'); return data;
      })));
      if (signal.aborted) return;
      const messages: string[] = [];
      const names = ['当前包目录', '历史安装记录', '计算节点'];
      const list = (i: number) => {
        const result = results[i];
        if (result.status === 'fulfilled') return result.value;
        messages.push(`${names[i]}未能读取，当前清单可能不完整。`); return [];
      };
      setCatalog(list(0) as CatalogPack[]); setInventory(list(1) as InventoryPack[]);
      const nextNodes = list(2) as ComputeNode[]; setNodes(nextNodes);
      setNodeId(current => nextNodes.some(n => n.node_id === current) ? current : nextNodes[0]?.node_id ?? '');
      if (!signal.aborted) { setWarnings(messages); setLoading(false); }
      // Paint the requested package first. Parsing unrelated manifests must not
      // hold a direct package URL behind the complete navigation catalog.
      if (priorityKey && !signal.aborted) {
        try {
          const complete = await read<CatalogPack[]>('/api/pack-catalog', signal);
          if (!Array.isArray(complete)) throw new Error('invalid_list');
          if (!signal.aborted) setCatalog(complete);
        } catch {
          if (!signal.aborted) setWarnings(current => [...current, '完整包目录未能读取，请刷新后重试。']);
        }
      }
    })();
    return () => controller.abort();
  }, [revision]);

  const rawEntries = useMemo(() => buildPackEntries(catalog, inventory, workflows, registry), [catalog, inventory, workflows, registry]);
  const capabilityGraph = useMemo(() => ({nodes: [...rawEntries.map(entry => ({
    id: entry.key, kind: 'algorithm', algorithm_name: entry.name, algorithm_version: entry.version,
    assigned_node_id: nodeId || null, params: {}, position: {x: 0, y: 0},
  })), ...workflows.flatMap(workflow => workflow.graph.nodes.map(node => ({...node, id: `usage:${workflow.workflow_id}:${node.id}`})))], edges: []}) as WorkflowGraph, [rawEntries, workflows, nodeId]);
  const checks = useCapabilityChecks(capabilityGraph, String(revision), !loading && !!nodeId && rawEntries.length > 0);
  const entries = useMemo(() => buildPackEntries(catalog, inventory, workflows.map(workflow => ({...workflow,
    check: {...workflow.check, issues: workflow.check?.issues ?? [], environment: Object.fromEntries(workflow.graph.nodes.flatMap(node => {
      const report = checks.reports[`usage:${workflow.workflow_id}:${node.id}`];
      return report ? [[node.id, report]] : [];
    }))},
  })), registry), [catalog, inventory, workflows, registry, checks.reports]);
  const displayName = useMemo(() => packDisplayNames(entries.map(entry => entry.name)), [entries]);
  const statusKey = (entry: PackEntry) => `${nodeId}:${entry.key}`;
  const reasons = (entry: PackEntry) => packProblemReasons(entry, checks.reports[entry.key], central[statusKey(entry)]?.data?.state === 'hash_mismatch'
    ? 'hash_mismatch' : central[statusKey(entry)]?.data?.registry_status);
  const problem = (entry: PackEntry) => reasons(entry).length > 0;
  const statusLabel = (entry: PackEntry) => reasons(entry).join('；') || packEnvironmentStatus(entry, checks.reports[entry.key]);
  const summary=localOverview(entries,nodeId,checks.reports,problem);
  const categoryEntries=entries.filter(entry=>{
    const path=entry.catalog?.category?.join('/') || 'misc';
    return !category || path===category || path.startsWith(`${category}/`);
  });
  const sectionEntries=(section==='category'?categoryEntries:entries).filter(entry=>section==='attention'?problem(entry):section==='missing'?checks.reports[entry.key]?.status==='missing':section==='undeclared'?isUndeclared(entry):true);
  const selectedEntry=section==='category' ? entries.find(entry=>entry.key===selected) : undefined;
  const healthPending=loading || checks.loading || !nodeId || entries.some(entry=>!checks.reports[entry.key] || checks.reports[entry.key].status==='unknown');
  async function queryStatus(entry: PackEntry) {
    const key = statusKey(entry);
    setCentral(current => ({...current, [key]: {busy: true}}));
    try {
      const query = new URLSearchParams({name: entry.name, version: entry.version});
      const data = await read<CentralStatus>(`/api/nodes/${encodeURIComponent(nodeId)}/pack-status?${query}`);
      setCentral(current => ({...current, [key]: {data}}));
    } catch { setCentral(current => ({...current, [key]: {failed: true}})); }
  }

  const feedback = <>
    {warnings.length > 0 && <div role="alert" className="hl-pack-notice">{warnings.map(message => <p key={message}>{message}</p>)}<button disabled={loading} onClick={()=>setRevision(n=>n+1)}>重试读取</button></div>}
    {usage.error && <p role="alert">工作流引用暂不可用。<button onClick={usage.refresh}>重试引用查询</button></p>}
    {checks.error && <p role="alert">本地能力暂时无法查询。<button onClick={checks.refresh}>重试环境检查</button></p>}
    {loading ? <p role="status">正在读取本地包…</p> : checks.loading && <p role="status">环境检查中…</p>}
  </>;
  const renderPackage = (entry: PackEntry) => {
    const selectedNode = nodes.find(node => node.node_id === nodeId);
    const installed = !!nodeId && installedOn(entry, nodeId);
    const current = central[statusKey(entry)];
    const yanked = current?.data?.registry_status === 'published' ? false : current?.data?.registry_status === 'yanked' || entry.release?.yanked === true;
    const hasRegistry = !!entry.catalog?.publication || !!entry.release;
    const report = checks.reports[entry.key];
    const requires = entry.catalog ? entry.catalog.requires : entry.release?.requires;
    const source = entry.release?.source ?? String(entry.catalog?.publication?.source ?? '');
    const historicalNodes = entry.inventory.filter(item => !entry.catalog?.node_ids.includes(item.node_id));
    const canInstall = !entry.version.endsWith('+') && !entry.name.startsWith('@builtin/') && !yanked;
    return <article className="hl-local-package-page" data-local-page="package" key={entry.key} data-pack-key={entry.key}>
      <nav className="hl-local-breadcrumb" aria-label="包路径"><button onClick={()=>navigateLocal('category','',entry.catalog?.category?.join('/') || 'misc')}>{entry.catalog?.category?.join(' / ') || 'misc'}</button><span aria-hidden="true">/</span><span>{displayName(entry.name)}</span></nav>
      <header><div><h1>{displayName(entry.name)}</h1><span className="hl-pack-version">{entry.version}</span></div>
        <span className="hl-pack-state" data-problem={problem(entry)}>{loading || checks.loading ? '检查中' : statusLabel(entry)}</span></header>
      {feedback}
      {entry.catalog?.description && <p className="hl-local-description">{entry.catalog.description}</p>}
      <dl className="hl-pack-facts">
        <div className="hl-pack-code-identity"><dt>代码身份</dt><dd><EvidenceValue value={current?.data?.code_sha256 ?? entry.catalog?.publication?.code_sha256 ?? '尚未校验；查询当前发布状态可读取本地代码身份'} /></dd></div>
        <div><dt>来源</dt><dd>{hasRegistry ? '注册表' : entry.catalog ? '本地' : '来源未确认'}{(source || entry.catalog?.source_dir || entry.catalog?.manifest_path) && <EvidenceValue value={source || entry.catalog?.source_dir || entry.catalog?.manifest_path} />}</dd></div>
        <div><dt>安装状态</dt><dd>{!nodeId ? '请先选择检查目标' : installed ? '当前节点已发现' : entry.inventory.some(item => item.node_id === nodeId) ? '仅有历史安装记录，当前未确认' : '当前节点未发现'}
          <small>{entry.catalog?.node_ids.length ? `在线提供：${entry.catalog.node_ids.map(id => nodes.find(n => n.node_id === id)?.node_name || id).join('、')}` : '当前没有在线节点提供'}
            {historicalNodes.length > 0 && `；也曾装于 ${historicalNodes.map(item => item.node_name || item.node_id).join('、')}（${historicalNodes.some(item => !item.online) ? '离线' : '历史记录'}）`}
          </small></dd></div>
        <div><dt>Yank 状态</dt><dd>{current?.data?.registry_status === 'yanked' ? '已 yank · 禁止新安装' : current?.data?.registry_status === 'published' ? '未 yank · 已发布' : entry.release ? entry.release.yanked ? '已 yank · 禁止新安装' : '未 yank（加载注册表时）' : '未查询，不能从安装记录推断当前状态'}
          <button disabled={!nodeId || current?.busy} onClick={() => void queryStatus(entry)}>{current?.busy ? '查询中…' : '查询当前发布状态'}</button>
          {current?.failed && <small role="status">查询失败：检查节点连接及注册表支持后重试。</small>}
          {current?.data?.state === 'hash_mismatch' && <small role="alert">代码哈希不匹配，请处理身份差异后再运行。</small>}
          {current?.data?.registry_status === 'unknown' && <small>中央状态未知，不代表已 yank；本地包仍可使用。</small>}</dd></div>
      </dl>
      <details className="hl-pack-section" open><summary>环境 · {report ? ({undeclared:'未声明要求',missing:'声明的能力缺失',unknown:'环境未校验',satisfied:'已声明的可探测项满足'} as const)[report.status] : nodeId ? '尚未完成检查' : '未选择计算节点'}</summary>
        <EnvironmentEvidence report={report} headingLabel={report?.status === 'unknown' ? '环境未校验' : undefined} />
        {requires != null && <details><summary>声明的 requires</summary><EvidenceValue value={requires} /></details>}
      </details>
      <details className="hl-pack-section" open><summary>使用情况与配方检查{usage.loading ? ' · 引用加载中…' : usage.error ? ' · 引用暂不可用' : ` · ${entry.usages.length} 个节点 / ${new Set(entry.usages.map(u => u.workflow.workflow_id)).size} 条工作流`}</summary>
        {!usage.loading && !usage.error && !entry.usages.length && <p>已读取的工作流草稿中没有引用此版本。</p>}
        {entry.usages.map(({workflow, node, issues}) => <section className="hl-pack-usage" key={`${workflow.workflow_id}:${node.id}`}>
          <div><button onClick={() => onOpenWorkflow(workflow.workflow_id)}>打开 {workflow.name}</button><strong>节点 {node.id}</strong><span>计算节点：{node.assigned_node_id ?? '未分配'}</span></div>
          {workflow.checkFailed ? <p>配方检查未完成，请刷新后重试。</p> : issues.length ? <ul>{issues.map((issue, index) => <li key={index}>{recipeIssueLabel(issue)}</li>)}</ul> : <p>未发现包依赖、变量或参数问题。</p>}
          {workflow.check?.environment?.[node.id] && <details><summary>此工作流所分配节点的环境</summary><EnvironmentEvidence report={workflow.check.environment[node.id]} headingLabel={workflow.check.environment[node.id].status === 'unknown' ? '环境未校验' : undefined} /></details>}
          {Object.keys(node.param_bindings ?? {}).length > 0 && <details><summary>变量绑定与值</summary><dl className="hl-evidence-rows">{Object.entries(node.param_bindings ?? {}).map(([parameter, variable]) => <div key={parameter}><dt>{parameter} → {variable}</dt><dd>
            {!workflow.graph.variables?.[variable] ? '变量未声明' : <><span>{workflow.graph.variables[variable].type} · {Object.prototype.hasOwnProperty.call(workflow.graph.variable_values ?? {}, variable) ? '本地值' : '共享默认值'}</span><EvidenceValue value={Object.prototype.hasOwnProperty.call(workflow.graph.variable_values ?? {}, variable) ? workflow.graph.variable_values?.[variable] : workflow.graph.variables[variable].default} /></>}
          </dd></div>)}</dl></details>}
        </section>)}
      </details>
      <details className="hl-pack-section"><summary>安装与源码</summary>
        {entry.catalog && <OpenSourceButton pack={entry.catalog} computeNode={(installed ? selectedNode : nodes.find(node => entry.catalog?.node_ids.includes(node.node_id))) ?? null} variant="row" label="打开 manifest.yaml" />}
        <p>在社区视角选择包版本即可安装。也可复制 CLI 命令在计算节点执行；安装不会代装环境依赖。</p>
        {!canInstall ? <p>{yanked ? '该版本已 yank，不能新安装；已安装版本与历史运行不因此删除。' : '开发版或内置包不能作为注册表发行版安装。'}</p>
          : <><code className="hl-pack-command">{installCommand(entry.name, entry.version, source)}</code><button className="hl-evidence-primary" onClick={async () => {
            const ok = await writeToClipboard(installCommand(entry.name, entry.version, source));
            setCommandStatus(current => ({...current, [entry.key]: ok ? '安装命令已复制；尚未执行安装。' : '未能复制，请选中命令手动复制。'}));
          }}>复制安装命令</button><span role="status">{commandStatus[entry.key]}</span></>}
      </details>
    </article>;
  };

  const renderLocalPage = () => {
    const list = {displayName, statusLabel, usageStatus: usage.loading ? '工作流引用加载中…' : usage.error ? '工作流引用暂不可用' : '', onSelect: (key: string) => navigateLocal('category', key)};
    if (section === 'overview') return <LocalOverviewPage {...list} items={entries} search={search} onSearch={setSearch} loading={loading} feedback={feedback} />;
    if (section === 'category' && selected) {
      if (selectedEntry) return renderPackage(selectedEntry);
      const versionSeparator = selected.lastIndexOf('@');
      const name = versionSeparator > 0 ? selected.slice(0, versionSeparator) : selected;
      return <div data-local-page="package"><h1>{displayName(name)}</h1>{feedback}{!loading && <p className="hl-pack-empty">该包当前未发现，请重新选择。</p>}</div>;
    }
    if (section === 'category') return <LocalResultsPage {...list} items={categoryEntries} kind="category" title={category ? category.split('/').join(' / ') : '全部分类'} feedback={feedback} loading={loading} emptyMessage="该分类下暂无包。" />;
    return <LocalResultsPage {...list} {...localStatusPages[section]} items={sectionEntries} kind={section} feedback={feedback} loading={loading}
      emptyMessage={(healthPending || usage.loading || usage.error) && (section==='missing' || section==='attention') ? '检查尚未完成，暂未确认符合此筛选的包。' : '没有符合此筛选的包。'} />;
  };

  return <div className="hl-app-shell hl-pack-manager" style={{'--pack-sidebar-width': `${leftDock.value}px`} as React.CSSProperties}>
    <header className="hl-topbar hl-pack-topbar">
      <button type="button" onClick={onBack}
        data-tooltip="返回流程列表" aria-label="返回流程列表" aria-description="back to workflows"
        style={{...CONTROL_STYLE, fontWeight: 600, color: 'var(--text)', gap: 6, letterSpacing: '-0.01em'}}>
        <span aria-hidden="true" style={{color: 'var(--text-subtle)', fontSize: 'var(--fs-md)'}}>←</span>
        HoloLab
      </button><div className="hl-pack-view-heading"><span>· Pack Manager</span>
      <div className="hl-pack-tabs" role="tablist" aria-label="包视角">
        {(['local', 'community'] as const).map(tab => <button key={tab} id={`pack-tab-${tab}`} role="tab" aria-controls={`pack-panel-${tab}`} aria-selected={view === tab}
          onClick={() => {setSearch('');onNavigate(packManagerPath(tab,tab==='local'?'overview':'home'));}}>{tab === 'local' ? '本地安装' : '社区'}</button>)}
      </div></div>
      <ThemeToggle /></header>
    <main className="hl-pack-page">
      <div id="pack-panel-community" role="tabpanel" aria-labelledby="pack-tab-community" hidden={view !== 'community'}><CommunityPacks onNavigate={onNavigate} active={view === 'community'} catalog={catalog} nodes={nodes} onInstalled={() => setRevision(n => n + 1)} /></div>
      <div id="pack-panel-local" role="tabpanel" aria-labelledby="pack-tab-local" hidden={view !== 'local'}>
      <div className="hl-pack-workspace">
      <aside className="hl-dock hl-pack-palette hl-pack-sidebar">
      <div className="hl-dock-heading"><h2 className="hl-section-title">本地安装</h2><button disabled={loading} onClick={() => setRevision(n => n + 1)}>{loading ? '读取中…' : '刷新'}</button></div>
      <div className="hl-pack-directory hl-local-directory">
      <nav className="hl-community-index hl-pack-view-nav" aria-label="本地视图">
        {([['overview','总览'],['attention',`需要注意 (${summary.attention.length})`],['missing',`环境缺失 (${summary.missing.length})`],['undeclared',`未声明 (${summary.undeclared.length})`]] as const).map(([value,label])=><button key={value} aria-current={section===value?'page':undefined} onClick={()=>navigateLocal(value)}>{label}</button>)}
      </nav>
      <nav className="hl-local-tree" aria-label="本地包目录">
        <PackageTree items={entries.map(entry => ({key:entry.key,name:entry.name,version:entry.version,category:entry.catalog?.category ?? [], status:statusLabel(entry), problem:problem(entry)}))} query="" displayName={displayName}
          selected={section==='category'?selected:''} selectedCategory={section==='category'&&!selected?category:''}
          onSelect={key=>navigateLocal('category',key)} onSelectCategory={path=>navigateLocal('category','',path)} />
      </nav>
      {nodes.length > 1 ? <label>查看机器<select aria-label="查看机器" value={nodeId} onChange={e => setNodeId(e.target.value)}>{nodes.map(node => <option key={node.node_id} value={node.node_id}>{node.node_name || node.node_id}</option>)}</select></label> : <p className="hl-pack-boundary">{nodes.length ? `查看 ${nodes[0].node_name || nodes[0].node_id} 上的包` : '没有已连接的机器'}</p>}
      </div></aside>
      <section key={packManagerPath('local',section,{pack:selected,category})} className="hl-pack-detail" aria-label="本地包内容">
      {renderLocalPage()}
      </section></div></div>
    </main>
  </div>;
}
