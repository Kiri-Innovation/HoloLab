import {packDisplayNames} from './packDisplayName';
import {collapseRedundantCategory} from './packCategory';
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
import { EnvironmentEvidence, environmentLabel, useCapabilityChecks } from '../canvas/EnvironmentChecks';
import type { NodeStatusPresentation, StatusTone } from '../canvas/nodeStatus';
import { NodeStatusDot } from '../ui/NodeStatusTooltip';
import { EvidenceValue } from '../canvas/EvidenceValue';
import { OpenSourceButton } from '../canvas/OpenSourceButton';
import { writeToClipboard } from '../canvas/referenceAction';
import { centralStatusKey } from './centralStatusKey';
import {usePackUsage} from './usePackUsage';
import { buildPackEntries, compactPackStatus, hasEnvironmentDetails, installedOn, installCommand, packProblemReasons, packEnvironmentStatus, recipeIssueLabel,
  type InventoryPack, type PackEntry, type RegistryRelease } from './packManagerModel';
import './packManager.css';

async function read<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, {signal});
  if (!response.ok) throw new Error('request_failed');
  return response.json();
}
interface CentralComparison { state: 'identical'|'differs'|'absent'|'unknown'; central_code_sha256?: string; yanked?: boolean; source?: string; reason?: string | null }

function status(tone: StatusTone, conclusion: string, reason: string, action: string): NodeStatusPresentation {
  return {tone, conclusion, reason, action};
}

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
  const {view, localSection:section, pack:selected, category, tree}=route;
  const usage = usePackUsage(section==='category'?selected:'', revision, view==='local');
  const workflows = usage.items;
  const navigateLocal=(next:string,pack='',category='', nextTree=tree)=>{
    if(next==='overview')setSearch('');
    onNavigate(packManagerPath('local',next,{pack,category:nextTree==='category'?category:'',tree:nextTree==='scope'?'scope':''}));
  };
  useEffect(()=>{setSearch('');},[view]);
  const leftDock = useResizableSlot('hl-layout-left', 300, 180, 420);
  const registry = useMemo<RegistryRelease[]>(() => [], []);
  const [centralComparison, setCentralComparison] = useState<Record<string, CentralComparison>>({});
  const [centralLoading, setCentralLoading] = useState(false);
  const [commandStatus, setCommandStatus] = useState<Record<string, string>>({});

  useEffect(() => {
    const controller = new AbortController();
    const signal = controller.signal;
    setLoading(true); setWarnings([]);
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

  const comparisonKeys = useMemo(() => catalog.map(pack => centralStatusKey(pack.name, pack.version)).join(','), [catalog]);
  async function refreshCentralComparison(fresh=false) {
    if (!comparisonKeys) { setCentralComparison({}); return; }
    setCentralLoading(true);
    try {
      const suffix = fresh ? '&refresh=true' : '';
      const data = await read<Record<string, CentralComparison>>(`/api/packs/central-status?keys=${encodeURIComponent(comparisonKeys)}${suffix}`);
      setCentralComparison(data);
    } catch {
      // The central relationship must never make the local page unusable.
      setCentralComparison(Object.fromEntries(catalog.map(pack => [centralStatusKey(pack.name, pack.version), {state:'unknown', reason:'查询请求未完成'}])));
    } finally { setCentralLoading(false); }
  }
  useEffect(() => { void refreshCentralComparison(); }, [comparisonKeys]);

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
  const reasons = (entry: PackEntry) => packProblemReasons(entry, checks.reports[entry.key]);
  const problem = (entry: PackEntry) => reasons(entry).length > 0;
  const statusLabel = (entry: PackEntry) => reasons(entry).join('；') || packEnvironmentStatus(entry, checks.reports[entry.key]);
  const compactStatus = (entry: PackEntry) => compactPackStatus(entry, checks.reports[entry.key]);
  const categoryTrail = (entry: PackEntry) => {
    return collapseRedundantCategory({name: entry.name, category: entry.catalog?.category ?? []}).category;
  };
  const summary=localOverview(entries,nodeId,checks.reports,problem);
  const categoryEntries=entries.filter(entry=>{
    const path=entry.catalog?.category?.join('/') || 'misc';
    return !category || path===category || path.startsWith(`${category}/`);
  });
  const sectionEntries=(section==='category'?categoryEntries:entries).filter(entry=>section==='attention'?problem(entry):section==='missing'?checks.reports[entry.key]?.status==='missing':section==='undeclared'?isUndeclared(entry):true);
  const selectedEntry=section==='category' ? entries.find(entry=>entry.key===selected) : undefined;
  const healthPending=loading || checks.loading || !nodeId || entries.some(entry=>!checks.reports[entry.key] || checks.reports[entry.key].status==='unknown');
  const feedback = <>
    {warnings.length > 0 && <div role="alert" className="hl-pack-notice">{warnings.map(message => <p key={message}>{message}</p>)}<button disabled={loading} onClick={()=>setRevision(n=>n+1)}>重试读取</button></div>}
    {usage.error && <p role="alert">工作流引用暂不可用。<button onClick={usage.refresh}>重试引用查询</button></p>}
    {checks.error && <p role="alert">本地能力暂时无法查询。<button onClick={checks.refresh}>重试环境检查</button></p>}
    {loading ? <p role="status">正在读取本地包…</p> : checks.loading && <p role="status">环境检查中…</p>}
  </>;
  const renderPackage = (entry: PackEntry) => {
    const selectedNode = nodes.find(node => node.node_id === nodeId);
    const installed = !!nodeId && installedOn(entry, nodeId);
    const comparison = centralComparison[centralStatusKey(entry.name, entry.version)];
    const yanked = entry.release?.yanked === true || comparison?.yanked === true;
    const report = checks.reports[entry.key];
    const requires = entry.catalog ? entry.catalog.requires : entry.release?.requires;
    const showEnvironment = hasEnvironmentDetails(requires);
    const source = entry.release?.source ?? String(entry.catalog?.publication?.source ?? '');
    const canInstall = !entry.version.endsWith('+') && !entry.name.startsWith('@builtin/') && !yanked;
    const breadcrumbCategory = categoryTrail(entry);
    const registryStatus = centralLoading && !comparison ? status('neutral', '查询中', '正在查询社区注册表。', '稍候会自动显示比对结果。')
      : comparison?.state === 'identical' ? status('success', '社区版', `社区注册表：${comparison.source}\n代码哈希：${comparison.central_code_sha256}`, '本地内容与线上同名版本一致。')
      : comparison?.state === 'differs' ? status('warning', '与线上不同', `社区注册表：${comparison.source}\n线上哈希：${comparison.central_code_sha256}\n本地哈希：${entry.catalog?.code_sha256 ?? '不可用'}`, '本地修改可正常开发；安装或发布前请确认要保留哪一版。')
      : comparison?.state === 'absent' ? status('neutral', '本地包', '社区注册表没有同名同版本。', '这是本地定义的包，可照常开发和运行。')
      : status('neutral', '未查询', comparison?.reason ?? '尚未取得注册表结果。', '本地包仍可使用；可稍后刷新重试。');
    const installStatus = !nodeId ? status('neutral', '未选节点', '尚未选择用于查看安装状态的计算节点。', '选择一个计算节点后可确认此包是否已安装。')
      : installed ? status('success', '已安装', `当前节点已发现：${selectedNode?.node_name || nodeId}`, '可在当前节点运行此版本。')
      : entry.inventory.some(item => item.node_id === nodeId) ? status('warning', '仅历史记录', '存在历史安装记录，但当前节点未确认提供此包。', '检查节点连接或重新安装此版本。')
      : status('neutral', '未安装', `当前节点 ${selectedNode?.node_name || nodeId} 未发现此包。`, '可在社区页安装，或选择提供此包的节点。');
    const environmentStatus = !report ? status('neutral', '环境未查', '尚未取得当前节点的环境检查结果。', '选择节点后等待检查完成。')
      : report.status === 'satisfied' ? status('success', '环境就绪', '已声明且可探测的环境要求均满足。', '仍请按实际任务输入确认运行条件。')
      : report.status === 'undeclared' ? status('warning', '环境未声明', '包作者没有声明环境要求，实际环境尚未确认。', '运行前请确认依赖和硬件条件。')
      : report.status === 'missing' ? status('error', '缺依赖', report.issues.map(issue => `${issue.item}：需要 ${String(issue.required)}，当前 ${String(issue.actual)}`).join('\n'), '按提示补齐依赖或选择满足要求的节点。')
      : status('neutral', '环境未知', '环境检查未能确认结果。', '检查节点连接后重新检查。');
    const statuses = [registryStatus, installStatus, environmentStatus, ...(yanked ? [status('error', '已 yank', '该版本已被发布者撤回，不推荐新安装。', '已安装版本不会被删除；新任务请选择其他版本。')] : [])];
    return <article className="hl-local-package-page" data-local-page="package" key={entry.key} data-pack-key={entry.key}>
      <nav className="hl-local-breadcrumb" aria-label="包路径">{breadcrumbCategory.length > 0 && <><button onClick={()=>navigateLocal('category','',breadcrumbCategory.join('/'))}>{breadcrumbCategory.join(' / ')}</button><span aria-hidden="true">/</span></>}<span>{displayName(entry.name)}</span></nav>
      <header><div><h1>{`${entry.name}@${entry.version}`}</h1>{(breadcrumbCategory.length > 0 || loading || checks.loading || compactStatus(entry)) && <p className="hl-pack-identity-meta">{breadcrumbCategory.length > 0 && <>{breadcrumbCategory.join(' / ')}{(loading || checks.loading || compactStatus(entry)) && <span aria-hidden="true"> · </span>}</>}{(loading || checks.loading || compactStatus(entry)) && <span className="hl-pack-state" data-problem={problem(entry)}>{loading || checks.loading ? '检查中' : compactStatus(entry)}</span>}</p>}</div></header>
      {feedback}
      {entry.catalog?.description && <p className="hl-local-description">{entry.catalog.description}</p>}
      <section className="hl-pack-status-summary" aria-label="包状态">
        {statuses.map(item => <span className="hl-pack-status-chip" data-status-tone={item.tone} key={item.conclusion}>
          <NodeStatusDot status={item} /><span>{item.conclusion}</span>
        </span>)}
        <button className="hl-pack-status-refresh" disabled={centralLoading} onClick={()=>void refreshCentralComparison(true)}>{centralLoading ? '查询中…' : '刷新注册表'}</button>
      </section>
      {showEnvironment && <details className="hl-pack-section" open><summary>环境 · {report ? environmentLabel(report) : nodeId ? '尚未完成检查' : '未选择计算节点'}</summary>
        <EnvironmentEvidence report={report} headingLabel={report?.status === 'unknown' ? '环境未校验' : undefined} />
        {requires != null && <details><summary>声明的 requires</summary><EvidenceValue value={requires} /></details>}
      </details>}
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
      <div className="hl-local-tree-mode" role="tablist" aria-label="本地包分组方式">
        <button role="tab" aria-selected={tree==='category'} onClick={()=>navigateLocal(section,selected,category,'category')}>按分类</button>
        <button role="tab" aria-selected={tree==='scope'} onClick={()=>navigateLocal(section,selected,'','scope')}>按作用域</button>
      </div>
      <nav className="hl-local-tree" aria-label={tree==='category' ? '按分类的本地包目录' : '按作用域的本地包目录'}>
        <PackageTree items={entries.map(entry => {
          const comparison = centralComparison[centralStatusKey(entry.name, entry.version)];
          return {
            key: entry.key,
            name: entry.name,
            version: entry.version,
            category: tree === 'scope' ? [entry.scope] : entry.catalog?.category ?? [],
            status: comparison?.state === 'differs' ? '注册表内容不同' : compactStatus(entry),
            problem: problem(entry) || comparison?.state === 'differs',
          };
        })} query="" displayName={displayName}
          selected={section==='category'?selected:''} selectedCategory={tree==='category' && section==='category'&&!selected?category:''}
          onSelect={key=>navigateLocal('category',key,'',tree)} onSelectCategory={tree==='category' ? path=>navigateLocal('category','',path,tree) : undefined} collapseRedundantCategories={tree==='category'} />
      </nav>
      {nodes.length > 1 ? <label>查看机器<select aria-label="查看机器" value={nodeId} onChange={e => setNodeId(e.target.value)}>{nodes.map(node => <option key={node.node_id} value={node.node_id}>{node.node_name || node.node_id}</option>)}</select></label> : <p className="hl-pack-boundary">{nodes.length ? `查看 ${nodes[0].node_name || nodes[0].node_id} 上的包` : '没有已连接的机器'}</p>}
      </div></aside>
      <section key={packManagerPath('local',section,{pack:selected,category})} className="hl-pack-detail" aria-label="本地包内容">
      {renderLocalPage()}
      </section></div></div>
    </main>
  </div>;
}
