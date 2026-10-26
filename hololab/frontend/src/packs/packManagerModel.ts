import { canonicalPackageName, packageKey } from '../packageIdentity';
import type { CatalogPack, WorkflowGraph } from '../wire';
import type { EnvironmentReport } from '../canvas/EnvironmentChecks';

export interface RecipeIssue { node_id: string; code?: string; message: string }
export interface RecipeCheck { issues: RecipeIssue[]; environment?: Record<string, EnvironmentReport> }
export interface WorkflowUsage { updated_ts?: number; workflow_id: string; name: string; graph: WorkflowGraph; check?: RecipeCheck; checkFailed?: boolean }
export interface InventoryPack { name: string; version: string; node_id: string; node_name?: string; online: boolean }
export interface RegistryRelease { name: string; version: string; yanked: boolean; requires?: unknown; source: string; code_sha256?: string }
export interface PackEntry {
  key: string; name: string; version: string; scope: string;
  catalog?: CatalogPack; inventory: InventoryPack[]; release?: RegistryRelease;
  usages: { workflow: WorkflowUsage; node: WorkflowGraph['nodes'][number]; issues: RecipeIssue[] }[];
}
export function buildPackEntries(catalog: CatalogPack[], inventory: InventoryPack[], workflows: WorkflowUsage[], releases: RegistryRelease[] = []): PackEntry[] {
  const entries = new Map<string, PackEntry>();
  const entry = (name: string, version: string) => {
    const key = packageKey(name, version);
    if (!entries.has(key)) {
      const canonical = canonicalPackageName(name);
      entries.set(key, {key, name: canonical, version, scope: canonical.split('/')[0], inventory: [], usages: []});
    }
    return entries.get(key)!;
  };
  for (const pack of inventory) entry(pack.name, pack.version).inventory.push(pack);
  for (const pack of catalog) entry(pack.name, pack.version).catalog = pack;
  for (const release of releases) entry(release.name, release.version).release = release;
  for (const workflow of workflows) for (const node of workflow.graph.nodes) {
    if ((node.kind ?? 'algorithm') !== 'algorithm') continue;
    entry(node.algorithm_name, node.algorithm_version).usages.push({workflow, node,
      issues: (workflow.check?.issues ?? []).filter(issue => issue.node_id === node.id && issue.code !== 'environment')});
  }
  return [...entries.values()].sort((a, b) => a.key.localeCompare(b.key));
}
export function installedOn(entry: PackEntry, nodeId: string) {
  return !!entry.catalog?.node_ids.includes(nodeId) || entry.inventory.some(p => p.node_id === nodeId && p.online);
}
export function packProblemReasons(entry: PackEntry, report?: EnvironmentReport, registryStatus?: string): string[] {
  const reasons: string[] = [];
  if (!entry.catalog && !entry.inventory.some(p => p.online) && entry.usages.length > 0)
    reasons.push(`${new Set(entry.usages.map(u => u.workflow.workflow_id)).size} 条工作流引用但包未安装`);
  if ((registryStatus !== 'published' && entry.release?.yanked === true) || registryStatus === 'yanked')
    reasons.push('版本已 yank，不能新安装');
  if (registryStatus === 'hash_mismatch') reasons.push('代码哈希不匹配');
  const missingLabel = (value: EnvironmentReport) => {
    const items = [...new Set(value.issues.map(issue => issue.item).filter(Boolean))];
    return items.length ? `缺少环境：${items.join('、')}` : '缺少已声明的环境能力';
  };
  if (report?.status === 'missing') reasons.push(missingLabel(report));
  for (const usage of entry.usages) {
    for (const issue of usage.issues) reasons.push(recipeIssueLabel(issue));
    if (usage.workflow.checkFailed) reasons.push('工作流配方检查未完成，请重试');
    const environment = usage.workflow.check?.environment?.[usage.node.id];
    if (environment?.status === 'missing') reasons.push(`工作流运行节点${missingLabel(environment)}`);
  }
  return [...new Set(reasons)];
}
export function packHasProblem(entry: PackEntry, report?: EnvironmentReport, registryStatus?: string) {
  return packProblemReasons(entry, report, registryStatus).length > 0;
}
export function packEnvironmentStatus(entry: PackEntry, report?: EnvironmentReport) {
  if (report?.status === 'undeclared' || (!report && entry.catalog && entry.catalog.requires == null)) return '未声明环境要求';
  if (!report || report.status === 'unknown') return '环境未校验';
  if (entry.usages.some(u => u.workflow.check?.environment?.[u.node.id]?.status === 'unknown')) return '工作流运行节点环境未校验';
  return report.status === 'missing' ? '缺少已声明的环境能力' : '已声明的可探测项满足';
}
export function recipeIssueLabel(issue: RecipeIssue) {
  switch (issue.code) {
    case 'missing_package': return '包未发现；请检查安装位置与计算节点连接。';
    case 'missing_parameter': return issue.message;
    case 'resolution': return '变量或参数无法解析；检查声明、绑定、本地值与类型。';
    case 'identity_unverified': return '本地发布身份未记录，执行前需要校验。';
    default: return '配方有待处理项，请检查该节点的声明与参数。';
  }
}
export function installCommand(name: string, version: string, source = '') {
  const quote = (value: string) => `'${value.replaceAll("'", "'\\''")}'`;
  return `hololab pack install ${quote(`${name}@${version}`)}${source ? ` --source ${quote(source)}` : ''}`;
}
