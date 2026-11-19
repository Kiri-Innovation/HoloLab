import {beforeAll, describe, it, expect} from 'vitest';
import {captureInterface, capturePersistedInterface, configureInterfacePersistence, initializeInterfaceSnapshots, readInterface, recoverHistoricalInterface, type HistoryAPI} from './interfaceSnapshot';
import {samePorts,replacementCandidates} from './algorithmReplacement';
import {applyOps,diffDocument,type DocumentState} from './workflowPatch';
import type {CatalogPack,GraphNode,SnapshotDetail,JobDetail} from '../wire';
const h = (c:string)=>c.repeat(64);
const manifest = {name:'k4d/init',version:'0.1.7',inputs:{colmap_4d:{any_of:[{tags:['colmap4d'],arrayed:false},{tags:['colmap-folder'],arrayed:true,dim_labels:['frame']}],storage:'dir'}},outputs:{result:{tags:['ftgspp-initialization'],scalar:true,storage:'dir'}},params:{gaussians:{type:'int',default:2000000}}};
const identity = ()=>({source:'k4d/init@0.1.7',sha256:h('a'),manifest:{sha256:h('b'),normalized:structuredClone(manifest)}});
const node = ()=>({id:'init-slot',algorithm_name:'k4d/init',algorithm_version:'0.1.7',position:{x:0,y:0},params:{gaussians:123},assigned_node_id:null} as GraphNode);
const job = ()=>({job_id:'old-job',workflow_id:'w',graph_node_id:'init-slot',algorithm_name:'k4d/init',algorithm_version:'0.1.7',state:'done',code_identity:identity(),snapshot_id:'NOT-THE-ATTRIBUTING-SNAPSHOT'} as unknown as JobDetail);
const snapshot = ()=>({workflow_id:'w',snapshot_id:'s',graph:{nodes:[node()],edges:[]},jobs:[job()],code_evidence:{'init-slot':{status:'unrecorded',code_identity:null}}} as unknown as SnapshotDetail);
const api = (snap=snapshot(),detail=job()): HistoryAPI=>({runs:async()=>[{snapshot_id:'s',workflow_id:'w'}],snapshot:async()=>snap,job:async()=>detail});
beforeAll(initializeInterfaceSnapshots);
describe('interface evidence',()=>{
 it('recovers the exact attributed node and closed union despite missing downstream packs',async()=>{
  const result=await recoverHistoricalInterface('w',node(),api()); expect(result.pack).toBeDefined();
  const p=result.pack!; expect(p.inputs.colmap_4d.any_of).toHaveLength(2);expect(p.arrayable).toBe(false);
  const next={...p,version:'0.1.8'};expect(replacementCandidates(p,[next])).toEqual([next]);
  const reordered={...next,inputs:{colmap_4d:{...next.inputs.colmap_4d,any_of:[...next.inputs.colmap_4d.any_of!].reverse()}}};
  expect(samePorts(p,reordered)).toBe(true);
  for(const bad of [{...next,arrayable:true},{...next,inputs:{colmap_4d:{...next.inputs.colmap_4d,any_of:[{tags:['colmap-folder'],arrayed:true,dim_labels:['camera']}]}}}]) expect(samePorts(p,bad)).toBe(false);
  const copy=JSON.parse(JSON.stringify(result.snapshot));expect(readInterface(node(),copy).pack?.inputs).toEqual(p.inputs);
  expect(readInterface({...node(),algorithm_version:'0.1.8'},copy).reason).toMatch(/身份/);
  copy.declaration_json+=' ';expect(readInterface(node(),copy).reason).toMatch(/校验/);
 });
 it('rejects missing, wrong-node, wrong-workflow and forged identities',async()=>{
  const empty=snapshot();empty.jobs=[];expect((await recoverHistoricalInterface('w',node(),api(empty))).pack).toBeUndefined();
  const other=snapshot();other.jobs[0].graph_node_id='other';expect((await recoverHistoricalInterface('w',node(),api(other))).pack).toBeUndefined();
  const foreign=job();foreign.workflow_id='other';expect((await recoverHistoricalInterface('w',node(),api(snapshot(),foreign))).reason).toMatch(/身份/);
  const forged=job();(forged.code_identity as any).manifest.normalized.version='0.1.8';expect((await recoverHistoricalInterface('w',node(),api(snapshot(),forged))).reason).toMatch(/身份/);
  const pinned={...node(),code_identity:{sha256:h('c'),manifest:{sha256:h('b')}}};expect((await recoverHistoricalInterface('w',pinned,api())).reason).toMatch(/身份/);
 });
 it('rejects conflicting jobs and recorded snapshot identity disagreement',async()=>{
  const s=snapshot();s.jobs.push({...s.jobs[0],job_id:'other-job'});const a=api(s);a.job=async id=>{const j=job();j.job_id=id;if(id==='other-job')(j.code_identity as any).sha256=h('c');return j;};
  expect((await recoverHistoricalInterface('w',node(),a)).reason).toMatch(/矛盾/);
  const pinned=snapshot();pinned.code_evidence!['init-slot']={status:'recorded',code_identity:{sha256:h('c')},effective_params:{}};
  expect((await recoverHistoricalInterface('w',node(),api(pinned))).reason).toMatch(/快照/);
 });
 it('preserves declaration, extensions and unrelated concurrent fields via field patches',async()=>{
  const p=(await recoverHistoricalInterface('w',node(),api())).pack!;
  const before:DocumentState={name:'x',graph:{nodes:[{...node(),extension:{future:null}} as any],edges:[]}};
  const after=structuredClone(before);after.graph.nodes[0].interface_snapshot=captureInterface(p);after.graph.nodes[0].params.gaussians=456;
  const ops=diffDocument(before,after);expect(ops.some(op=>op.op === 'node.set' && op.value.interface_snapshot)).toBe(true);
  const remote=structuredClone(before);remote.graph.nodes[0].position={x:999,y:1};remote.graph.nodes[0].params.unrelated=789;
  const rebased=applyOps(remote,ops);expect(rebased.graph.nodes[0]).toMatchObject({position:{x:999},params:{gaussians:456,unrelated:789},extension:{future:null}});
  expect(readInterface(node(),rebased.graph.nodes[0].interface_snapshot).pack).toBeDefined();
 });
 it('gates new persistence on legacy gateways while keeping historical recovery available',async()=>{
  const p=(await recoverHistoricalInterface('w',node(),api())).pack!;
  configureInterfacePersistence(false);
  try {expect(capturePersistedInterface(p)).toBeUndefined();expect(captureInterface(p)).toBeDefined();}
  finally {configureInterfacePersistence(true);}
 });
 it('refuses empty-tag and unbound/invalid declarations instead of treating them as wildcards',()=>{
  const p={name:'k4d/init',version:'0.1.7',manifest_hash:h('b'),arrayable:false,inputs:{in:{tags:[]}},outputs:{},params:{}} as unknown as CatalogPack;
  expect(readInterface(node(),captureInterface(p)).reason).toMatch(/不完整/);
  expect(readInterface(node(),{schemaVersion:1,declaration_json:'{}',sha256:h('f')}).pack).toBeUndefined();
 });
});
