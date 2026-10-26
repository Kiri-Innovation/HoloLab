import { canonicalPackageName, packageKey } from '../packageIdentity';
import type { CatalogPack, WorkflowGraph } from '../wire';
import type { EnvironmentReport } from '../canvas/EnvironmentChecks';

export interface RecipeIssue { node_id: string; code?: string; message: string }
export interface RecipeCheck { issues: RecipeIssue[]; environment?: Record<string, EnvironmentReport> }
export interface WorkflowUsage { workflow_id: string; name: string; graph: WorkflowGraph; check?: RecipeCheck; checkFailed?: boolean }
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
export function packHasProblem(entry: PackEntry, report?: EnvironmentReport, registryStatus?: string) {
  return (!entry.catalog && !entry.inventory.some(p => p.online) && entry.usages.length > 0)
    || (registryStatus !== 'published' && entry.release?.yanked === true) || registryStatus === 'yanked' || registryStatus === 'hash_mismatch'
    || report?.status === 'missing' || report?.status === 'unknown'
    || entry.usages.some(u => u.issues.length > 0 || u.workflow.checkFailed
      || ['missing', 'unknown'].includes(u.workflow.check?.environment?.[u.node.id]?.status ?? ''));
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
