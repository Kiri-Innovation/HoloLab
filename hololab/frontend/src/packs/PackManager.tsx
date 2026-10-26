import { CommunityPacks } from './CommunityPacks';
import { useEffect, useMemo, useState } from 'react';
import type { CatalogPack, ComputeNode, WorkflowGraph } from '../wire';
import type { WorkflowSummary } from '../api';
import { ThemeToggle } from '../theme/ThemeToggle';
import { EnvironmentEvidence, useCapabilityChecks } from '../canvas/EnvironmentChecks';
import { EvidenceValue } from '../canvas/EvidenceValue';
import { OpenSourceButton } from '../canvas/OpenSourceButton';
import { writeToClipboard } from '../canvas/referenceAction';
import { checkRecipe } from './useRecipeChecks';
import { buildPackEntries, installedOn, installCommand, packHasProblem, recipeIssueLabel,
  type InventoryPack, type PackEntry, type RegistryRelease, type WorkflowUsage } from './packManagerModel';
import './packManager.css';

async function read<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, {signal});
  if (!response.ok) throw new Error('request_failed');
  return response.json();
}
interface CentralStatus { code_sha256?: string; registry_status?: string; state?: string; source?: string; publication?: Record<string, unknown> }

export function PackManager({onBack, onOpenLibrary, onOpenWorkflow}: {
  onBack: () => void; onOpenLibrary: () => void; onOpenWorkflow: (id: string) => void;
}) {
  const [catalog, setCatalog] = useState<CatalogPack[]>([]);
  const [inventory, setInventory] = useState<InventoryPack[]>([]);
  const [nodes, setNodes] = useState<ComputeNode[]>([]);
  const [workflows, setWorkflows] = useState<WorkflowUsage[]>([]);
  const [loading, setLoading] = useState(true);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [revision, setRevision] = useState(0);
  const [nodeId, setNodeId] = useState('');
  const [search, setSearch] = useState('');
  const [problemsOnly, setProblemsOnly] = useState(false);
  const [groupScope, setGroupScope] = useState(true);
  const [view, setView] = useState<'local' | 'community'>('local');
  const registry = useMemo<RegistryRelease[]>(() => [], []);
  const [central, setCentral] = useState<Record<string, {data?: CentralStatus; busy?: boolean; failed?: boolean}>>({});
  const [commandStatus, setCommandStatus] = useState<Record<string, string>>({});

  useEffect(() => {
    const controller = new AbortController();
    const signal = controller.signal;
    setLoading(true); setWarnings([]); setCentral({});
    void (async () => {
      const requests = ['/api/pack-catalog', '/api/packs', '/api/nodes', '/api/workflows'];
      const results = await Promise.allSettled(requests.map(url => read<unknown[]>(url, signal).then(data => {
        if (!Array.isArray(data)) throw new Error('invalid_list'); return data;
      })));
      if (signal.aborted) return;
      const messages: string[] = [];
      const names = ['当前包目录', '历史安装记录', '计算节点', '工作流列表'];
      const list = (i: number) => {
        const result = results[i];
        if (result.status === 'fulfilled') return result.value;
        messages.push(`${names[i]}未能读取，当前清单可能不完整。`); return [];
      };
      setCatalog(list(0) as CatalogPack[]); setInventory(list(1) as InventoryPack[]);
      const nextNodes = list(2) as ComputeNode[]; setNodes(nextNodes);
      setNodeId(current => nextNodes.some(n => n.node_id === current) ? current : nextNodes[0]?.node_id ?? '');
      const summaries = list(3) as WorkflowSummary[];
      const usages: WorkflowUsage[] = [];
      // Bound fan-out: loading every saved workflow must not saturate the gateway.
      let cursor = 0;
      await Promise.all(Array.from({length: Math.min(4, summaries.length)}, async () => {
        while (cursor < summaries.length && !signal.aborted) {
          const summary = summaries[cursor++];
          try {
            const workflow = await read<WorkflowUsage>(`/api/workflows/${encodeURIComponent(summary.workflow_id)}`, signal);
            if (!workflow.graph?.nodes) throw new Error('missing_graph');
            try { workflow.check = await checkRecipe(workflow.graph, signal); }
            catch { workflow.checkFailed = true; }
            usages.push(workflow);
          } catch { messages.push(`工作流「${summary.name}」未能读取，使用情况尚不完整。`); }
        }
      }));
      if (!signal.aborted) { setWorkflows(usages); setWarnings(messages); setLoading(false); }
    })();
    return () => controller.abort();
  }, [revision]);

  const entries = useMemo(() => buildPackEntries(catalog, inventory, workflows, registry), [catalog, inventory, workflows, registry]);
  const capabilityGraph = useMemo(() => ({nodes: entries.map(entry => ({
    id: entry.key, kind: 'algorithm', algorithm_name: entry.name, algorithm_version: entry.version,
    assigned_node_id: nodeId || null, params: {}, position: {x: 0, y: 0},
  })), edges: []}) as WorkflowGraph, [entries, nodeId]);
  const checks = useCapabilityChecks(capabilityGraph, String(revision), !loading && !!nodeId && entries.length > 0);
  const statusKey = (entry: PackEntry) => `${nodeId}:${entry.key}`;
  const problem = (entry: PackEntry) => packHasProblem(entry, checks.reports[entry.key], central[statusKey(entry)]?.data?.state === 'hash_mismatch'
    ? 'hash_mismatch' : central[statusKey(entry)]?.data?.registry_status);
  const visible = entries.filter(entry => (!problemsOnly || problem(entry)) && `${entry.name}@${entry.version}`.toLowerCase().includes(search.toLowerCase()));
  const groups = new Map<string, PackEntry[]>();
  for (const entry of visible) { const group = groupScope ? entry.scope : '全部包'; groups.set(group, [...groups.get(group) ?? [], entry]); }

  async function queryStatus(entry: PackEntry) {
    const key = statusKey(entry);
    setCentral(current => ({...current, [key]: {busy: true}}));
    try {
      const query = new URLSearchParams({name: entry.name, version: entry.version});
      const data = await read<CentralStatus>(`/api/nodes/${encodeURIComponent(nodeId)}/pack-status?${query}`);
      setCentral(current => ({...current, [key]: {data}}));
    } catch { setCentral(current => ({...current, [key]: {failed: true}})); }
  }

  return <div className="hl-app-shell hl-pack-manager">
    <header className="hl-topbar hl-pack-topbar"><strong>HoloLab</strong><span>· Pack Manager</span>
      <button onClick={onBack}>← Workflows</button><button onClick={onOpenLibrary}>Library</button><ThemeToggle /></header>
    <main className="hl-pack-page">
      <div className="hl-pack-heading"><div><h1>包管理</h1><p>统一查看包的安装、环境和使用情况。画布只负责编辑与运行。</p></div>
        <button disabled={loading} onClick={() => setRevision(n => n + 1)}>{loading ? '正在读取…' : '刷新全部状态'}</button></div>
      <p className="hl-pack-boundary">社区浏览发布版本；本地查看本实例发现的包和已保存工作流引用。使用情况以已保存草稿为准，不包含未保存编辑与历史快照。</p>
      {warnings.length > 0 && <div role="alert" className="hl-pack-notice">{warnings.map(message => <p key={message}>{message}</p>)}</div>}
      <div className="hl-pack-tabs" role="tablist" aria-label="包视角">
        <button role="tab" aria-selected={view === 'local'} onClick={() => setView('local')}>本地</button>
        <button role="tab" aria-selected={view === 'community'} onClick={() => setView('community')}>社区</button>
      </div>
      <div hidden={view !== 'community'}><CommunityPacks catalog={catalog} nodes={nodes} onInstalled={() => setRevision(n => n + 1)} /></div>
      <div hidden={view !== 'local'}>
      <div className="hl-pack-filters"><input type="search" aria-label="搜索包" placeholder="搜索包名或版本" value={search} onChange={e => setSearch(e.target.value)} />
        <label>检查目标<select aria-label="检查目标计算节点" value={nodeId} onChange={e => setNodeId(e.target.value)}><option value="">选择计算节点</option>{nodes.map(node => <option key={node.node_id} value={node.node_id}>{node.node_name || node.node_id}</option>)}</select></label>
        <label><input type="checkbox" checked={problemsOnly} onChange={e => setProblemsOnly(e.target.checked)} />只看有问题的</label>
        <label><input type="checkbox" checked={groupScope} onChange={e => setGroupScope(e.target.checked)} />按 scope 分组</label></div>
      <div className="hl-pack-summary" role="status">{loading ? '正在汇总包与工作流…' : `${entries.length} 个包版本 · ${entries.filter(problem).length} 项待处理 · 显示 ${visible.length} 项`}
        {checks.loading && ' · 环境检查中…'}</div>
      <p className="hl-pack-boundary">未声明 requires 表示作者没有写要求，不是缺失，不会因此阻止运行。已声明但缺失的能力需要补齐；通过检查也不保证一定能运行。</p>
      {checks.error && <p role="alert">本地能力暂时无法查询，请检查计算节点连接后刷新。</p>}
      {!loading && !visible.length && <div className="hl-pack-empty">{entries.length ? '没有符合筛选条件的包。' : '还没有发现包。连接计算节点或加载注册表目录后，包会出现在这里。'}</div>}
      {[...groups].map(([scope, packs]) => <section className="hl-pack-group" key={scope}><h2>{scope}<span>{packs.length} 个版本</span></h2>
        {packs.map(entry => {
          const selectedNode = nodes.find(node => node.node_id === nodeId);
          const installed = !!nodeId && installedOn(entry, nodeId);
          const current = central[statusKey(entry)];
          const yanked = current?.data?.registry_status === 'published' ? false : current?.data?.registry_status === 'yanked' || entry.release?.yanked === true;
          const hasRegistry = !!entry.catalog?.publication || !!entry.release;
          const report = checks.reports[entry.key];
          const requires = entry.catalog ? entry.catalog.requires : entry.release?.requires;
          const source = entry.release?.source ?? String(entry.catalog?.publication?.source ?? '');
          const canInstall = !entry.version.endsWith('+') && !entry.name.startsWith('@builtin/') && !yanked;
          return <article className="hl-pack-card" key={entry.key} data-pack-key={entry.key}>
            <header><div><h3>{entry.name}</h3><span className="hl-pack-version">{entry.version}</span></div>
              <span className="hl-pack-state" data-problem={problem(entry)}>{problem(entry) ? '待处理' : loading || checks.loading ? '检查中' : !report ? '环境未校验' : report.status === 'undeclared' ? '环境未声明' : '无已知问题'}</span></header>
            <dl className="hl-pack-facts">
              <div className="hl-pack-code-identity"><dt>代码身份</dt><dd><EvidenceValue value={current?.data?.code_sha256 ?? entry.catalog?.publication?.code_sha256 ?? '尚未校验；查询当前发布状态可读取本地代码身份'} /></dd></div>
              <div><dt>来源</dt><dd>{hasRegistry ? '注册表' : entry.catalog ? '本地' : '来源未确认'}{(source || entry.catalog?.source_dir || entry.catalog?.manifest_path) && <EvidenceValue value={source || entry.catalog?.source_dir || entry.catalog?.manifest_path} />}</dd></div>
              <div><dt>安装状态</dt><dd>{!nodeId ? '请先选择检查目标' : installed ? '当前节点已发现' : entry.inventory.some(item => item.node_id === nodeId) ? '仅有历史安装记录，当前未确认' : '当前节点未发现'}
                <small>{entry.catalog?.node_ids.length ? `在线提供：${entry.catalog.node_ids.map(id => nodes.find(n => n.node_id === id)?.node_name || id).join('、')}` : entry.inventory.length ? '有历史记录；离线不等于已卸载' : '尚无本地安装记录'}</small></dd></div>
              <div><dt>Yank 状态</dt><dd>{current?.data?.registry_status === 'yanked' ? '已 yank · 禁止新安装' : current?.data?.registry_status === 'published' ? '未 yank · 已发布' : entry.release ? entry.release.yanked ? '已 yank · 禁止新安装' : '未 yank（加载注册表时）' : '未查询，不能从安装记录推断当前状态'}
                <button disabled={!nodeId || current?.busy} onClick={() => void queryStatus(entry)}>{current?.busy ? '查询中…' : '查询当前发布状态'}</button>
                {current?.failed && <small role="status">查询失败：检查节点连接及注册表支持后重试。</small>}
                {current?.data?.state === 'hash_mismatch' && <small role="alert">代码哈希不匹配，请处理身份差异后再运行。</small>}
                {current?.data?.registry_status === 'unknown' && <small>中央状态未知，不代表已 yank；本地包仍可使用。</small>}</dd></div>
            </dl>
            <details className="hl-pack-section" open={report?.status === 'missing'}><summary>环境 · {report ? ({undeclared:'未声明要求',missing:'声明的能力缺失',unknown:'无法确认',satisfied:'已声明的可探测项满足'} as const)[report.status] : nodeId ? '尚未完成检查' : '未选择计算节点'}</summary>
              <EnvironmentEvidence report={report} />
              {requires != null && <details><summary>声明的 requires</summary><EvidenceValue value={requires} /></details>}
            </details>
            <details className="hl-pack-section"><summary>使用情况与配方检查 · {entry.usages.length} 个节点 / {new Set(entry.usages.map(u => u.workflow.workflow_id)).size} 条工作流</summary>
              {!entry.usages.length && <p>已读取的工作流草稿中没有引用此版本。</p>}
              {entry.usages.map(({workflow, node, issues}) => <section className="hl-pack-usage" key={`${workflow.workflow_id}:${node.id}`}>
                <div><button onClick={() => onOpenWorkflow(workflow.workflow_id)}>打开 {workflow.name}</button><strong>节点 {node.id}</strong><span>计算节点：{node.assigned_node_id ?? '未分配'}</span></div>
                {workflow.checkFailed ? <p>配方检查未完成，请刷新后重试。</p> : issues.length ? <ul>{issues.map((issue, index) => <li key={index}>{recipeIssueLabel(issue)}</li>)}</ul> : <p>未发现包依赖、变量或参数问题。</p>}
                {workflow.check?.environment?.[node.id] && <details><summary>此工作流所分配节点的环境</summary><EnvironmentEvidence report={workflow.check.environment[node.id]} /></details>}
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
        })}
      </section>)}
      </div>
    </main>
  </div>;
}
