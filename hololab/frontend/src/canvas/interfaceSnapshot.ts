/** Portable, declaration-only evidence. Checksums detect corruption, not authorship.
 * Historical recovery trusts only server-returned attributed jobs, never a
 * latest-version guess, a same-name witness, or jobs.snapshot_id. */
import { createSHA256, type IHasher } from 'hash-wasm';
import type { CatalogPack, GraphNode, SnapshotDetail, JobDetail } from '../wire';
import { samePackage } from '../packageIdentity';

let hasher: IHasher | undefined;
let persistenceAvailable = true;
export function configureInterfacePersistence(available: boolean) { persistenceAvailable = available; }
export function capturePersistedInterface(pack: CatalogPack): InterfaceSnapshot | undefined {
  return persistenceAvailable ? captureInterface(pack) : undefined;
}
const ready = createSHA256().then(value => { hasher = value; });
export const initializeInterfaceSnapshots = () => ready;
export const stableJSON = (value: unknown): string => JSON.stringify(value, (_key, item) =>
  item && typeof item === 'object' && !Array.isArray(item)
    ? Object.fromEntries(Object.keys(item).sort().map(key => [key, item[key]])) : item);
const sha = (value: string) => hasher!.init().update(value).digest('hex');
const hash = (value: unknown): value is string => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const object = (value: unknown): value is Record<string, any> => !!value && typeof value === 'object' && !Array.isArray(value);

export interface InterfaceSnapshot {
  schemaVersion: 1;
  declaration_json: string;
  sha256: string;
}
export interface InterfaceEvidence {
  name: string; version: string; manifest_hash: string; code_sha256?: string | null;
  arrayable: boolean; inputs: CatalogPack['inputs']; outputs: CatalogPack['outputs']; params: CatalogPack['params'];
  source: { kind: 'catalog' } | { kind: 'job'; workflow_id: string; graph_node_id: string; job_ids: string[]; snapshot_ids: string[] };
}
export function captureInterface(pack: CatalogPack, source: InterfaceEvidence['source'] = {kind:'catalog'}): InterfaceSnapshot | undefined {
  if (!hasher || !hash(pack.manifest_hash) || typeof pack.arrayable !== 'boolean') return undefined;
  const evidence: InterfaceEvidence = {name:pack.name, version:pack.version, manifest_hash:pack.manifest_hash,
    code_sha256:pack.code_sha256 ?? null, arrayable:pack.arrayable, inputs:pack.inputs, outputs:pack.outputs, params:pack.params, source};
  const declaration_json = stableJSON(evidence);
  return {schemaVersion:1, declaration_json, sha256:sha(declaration_json)};
}
function validPort(port: any, input: boolean): boolean {
  if (!object(port) || !Array.isArray(port.tags) || !port.tags.every((x: unknown) => typeof x === 'string' && x.length)) return false;
  if (port.arrayed != null && typeof port.arrayed !== 'boolean' || port.scalar != null && typeof port.scalar !== 'boolean') return false;
  if (port.dim_labels != null && (!Array.isArray(port.dim_labels) || !port.dim_labels.every((x: unknown) => typeof x === 'string'))) return false;
  if (port.any_of != null) return input && Array.isArray(port.any_of) && port.any_of.length > 0
    && port.tags.length === 0 && port.any_of.every((v: any) => validPort({...v, any_of:undefined}, false));
  // A missing tag is unknown, not the explicit `any` wildcard.
  return port.tags.length > 0;
}
export function readInterface(node: Pick<GraphNode,'algorithm_name'|'algorithm_version'|'code_identity'> & {id?:string}, value: unknown): {pack?: CatalogPack; source?: InterfaceEvidence['source']; reason?: string} {
  if (!value) return {};
  if (!hasher || !object(value) || value.schemaVersion !== 1 || typeof value.declaration_json !== 'string'
      || !hash(value.sha256) || sha(value.declaration_json) !== value.sha256) return {reason:'保存的接口声明校验失败'};
  let evidence: InterfaceEvidence;
  try { evidence = JSON.parse(value.declaration_json); } catch { return {reason:'保存的接口声明无法解析'}; }
  if (!object(evidence) || typeof evidence.name !== 'string' || typeof evidence.version !== 'string' || !samePackage(node.algorithm_name,node.algorithm_version,evidence.name,evidence.version)
      || !hash(evidence.manifest_hash) || (evidence.code_sha256 != null && !hash(evidence.code_sha256))
      || typeof evidence.arrayable !== 'boolean' || !object(evidence.inputs) || !object(evidence.outputs) || !object(evidence.params)
      || !Object.values(evidence.inputs).every(p => validPort(p,true)) || !Object.values(evidence.outputs).every(p => validPort(p,false))
      || !object(evidence.source) || !['catalog','job'].includes(evidence.source.kind)) return {reason:'保存的接口声明不完整或包身份不匹配'};
  if (evidence.source.kind === 'job' && (!evidence.source.workflow_id || !evidence.source.graph_node_id || (node.id && evidence.source.graph_node_id !== node.id) || !evidence.source.job_ids?.length || !evidence.source.snapshot_ids?.length)) return {reason:'保存的历史声明缺少正确的任务归属'};
  const identity = node.code_identity as any;
  if (identity && (identity.source !== `${node.algorithm_name}@${node.algorithm_version}`
      || identity.sha256 !== evidence.code_sha256 || identity.manifest?.sha256 !== evidence.manifest_hash)) return {reason:'保存的接口与节点代码身份冲突'};
  return {source:evidence.source,pack:{...evidence, manifest_hash:evidence.manifest_hash, node_ids:[], description:null, category:[], docs:null, source_entry:null} as unknown as CatalogPack};
}

// Manifest -> catalog defaults. This is declaration normalization, not matching,
// triangulation, or execution. Preserve extension fields and union branches.
function manifestPort(raw: any, input: boolean): any {
  const port = {...raw, tags:raw.tags ?? [], arrayed:raw.arrayed ?? false, scalar:raw.scalar ?? false,
    dim_labels:raw.dim_labels ?? [], storage:raw.storage ?? 'dir'};
  if (input) {
    port.required = raw.required ?? true;
    port.require_axis_label_match = raw.require_axis_label_match ?? true;
    if (raw.any_of) port.any_of = raw.any_of.map((v: any) => ({...v,tags:v.tags ?? [],arrayed:v.arrayed ?? false,dim_labels:v.dim_labels ?? [],storage:v.storage ?? raw.storage ?? 'dir'}));
  }
  if (!input) port.dim_labels_drop_outer = raw.dim_labels_drop_outer ?? 0;
  return port;
}
function manifestPack(node: GraphNode, identity: any): CatalogPack | null {
  // content_scope deliberately excludes release version from normalized manifest.
  // The attributed job and identity.source above bind that version instead.
  const m = identity?.manifest?.normalized;
  if (!object(m) || !hash(identity.sha256) || !hash(identity.manifest.sha256)
      || identity.source !== `${node.algorithm_name}@${node.algorithm_version}`
      || !samePackage(node.algorithm_name,node.algorithm_version,m.name,m.version ?? node.algorithm_version)
      || !object(m.inputs ?? {}) || !object(m.outputs ?? {}) || (m.arrayable != null && typeof m.arrayable !== 'boolean')) return null;
  if (node.code_identity && ((node.code_identity as any).sha256 !== identity.sha256
      || (node.code_identity as any).manifest?.sha256 !== identity.manifest.sha256)) return null;
  const pack = {name:node.algorithm_name,version:node.algorithm_version,manifest_hash:identity.manifest.sha256,
    code_sha256:identity.sha256,arrayable:m.arrayable ?? false,
    inputs:Object.fromEntries(Object.entries(m.inputs ?? {}).map(([k,v])=>[k,manifestPort(v,true)])),
    outputs:Object.fromEntries(Object.entries(m.outputs ?? {}).map(([k,v])=>[k,manifestPort(v,false)])),params:m.params ?? {},node_ids:[]} as unknown as CatalogPack;
  return readInterface({...node,code_identity:undefined},captureInterface(pack)).pack ?? null;
}
export type HistoryAPI = {
  runs: (workflowId:string) => Promise<{snapshot_id:string;workflow_id:string}[]>;
  snapshot: (id:string) => Promise<SnapshotDetail>;
  job: (id:string) => Promise<JobDetail>;
};
export async function recoverHistoricalInterface(workflowId: string, node: GraphNode, api: HistoryAPI): Promise<{pack?:CatalogPack; snapshot?:InterfaceSnapshot; reason?:string}> {
  await ready;
  const runs = await api.runs(workflowId), records = new Map<string,{job:any; snapshotIds:string[]; pins:any[]}>();
  for (const run of runs) {
    if (run.workflow_id !== workflowId) return {reason:'历史运行归属不匹配'};
    const snap = await api.snapshot(run.snapshot_id);
    if (snap.workflow_id !== workflowId || snap.snapshot_id !== run.snapshot_id) return {reason:'历史快照归属不匹配'};
    const slot = snap.graph.nodes.find(n=>n.id === node.id);
    if (!slot || !samePackage(node.algorithm_name,node.algorithm_version,slot.algorithm_name,slot.algorithm_version)) continue;
    // Done jobs returned here are authoritative snapshot_jobs attributions.
    for (const job of snap.jobs.filter(j=>j.graph_node_id === node.id && j.state === 'done' && !j.parent_job_id)) {
      if (job.workflow_id !== workflowId || !samePackage(node.algorithm_name,node.algorithm_version,job.algorithm_name,job.algorithm_version)) return {reason:'历史任务归属或算法身份冲突'};
      const record = records.get(job.job_id) ?? {job,snapshotIds:[],pins:[]};
      const pin = snap.code_evidence?.[node.id]?.code_identity ?? slot.code_identity;
      if (pin) record.pins.push(pin);
      record.snapshotIds.push(snap.snapshot_id); records.set(job.job_id,record);
    }
  }
  const candidates: {pack:CatalogPack;jobId:string;snapshotIds:string[]}[] = [];
  for (const [jobId, record] of records) {
    const detail = await api.job(jobId);
    if (detail.job_id !== jobId || detail.graph_node_id !== node.id || detail.workflow_id !== workflowId
        || !samePackage(node.algorithm_name,node.algorithm_version,detail.algorithm_name,detail.algorithm_version)) return {reason:'历史任务详情身份冲突'};
    const identity = detail.code_identity;
    if (record.pins.some(pin => pin.sha256 !== identity?.sha256 || pin.manifest?.sha256 !== (identity as any)?.manifest?.sha256 || (pin.manifest?.normalized && stableJSON(pin.manifest.normalized) !== stableJSON((identity as any)?.manifest?.normalized)))) return {reason:'快照与已归属任务的代码身份冲突'};
    if (!identity) continue; // Older snapshots may genuinely predate evidence.
    const pack = manifestPack(node,identity);
    if (!pack) return {reason:'历史 manifest 缺失、无效或与节点代码身份冲突'};
    candidates.push({pack,jobId,snapshotIds:record.snapshotIds});
  }
  if (!candidates.length) return {reason:'此节点没有可验证的已运行历史接口声明'};
  const identities = new Set(candidates.map(c=>stableJSON([c.pack.manifest_hash,c.pack.code_sha256,c.pack.inputs,c.pack.outputs,c.pack.arrayable])));
  if (identities.size !== 1) return {reason:'此节点同版本存在多条矛盾的历史代码或接口声明，未自动选择'};
  const pack = candidates[0].pack;
  return {pack,snapshot:captureInterface(pack,{kind:'job',workflow_id:workflowId,graph_node_id:node.id,
    job_ids:candidates.map(c=>c.jobId),snapshot_ids:[...new Set(candidates.flatMap(c=>c.snapshotIds))]})};
}
