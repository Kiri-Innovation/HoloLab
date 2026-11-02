import {describe, expect, it} from 'vitest';
import type {WorkflowGraph} from '../wire';
import type {ExecutionAnalysis} from './executionAnalysis';
import {pendingAlgorithmChanges} from './pendingAlgorithmChanges';
import {analysisSignatures} from './analysisInputs';

const graph = {nodes: ['a','b','c'].map(id => ({id,kind:'algorithm',algorithm_name:'demo',algorithm_version:'1.0.0',params:{},position:{x:0,y:0}})),
  edges: [{id:'ab',source:'a',target:'b',sourceHandle:'out',targetHandle:'in'}]} as WorkflowGraph;
const evidence: ExecutionAnalysis = {revision:'old',graph,staleness:{a:null,b:null,c:null},diff:[],environment_params:{}};
function replaced() {
  const next = structuredClone(graph); next.nodes[0].algorithm_version = '2.0.0'; return next;
}
describe('pending algorithm changes', () => {
  it('invalidates the signature and marks replacement and downstream evidence immediately', () => {
    const next = replaced();
    expect(analysisSignatures(next).edit).not.toBe(analysisSignatures(graph).edit);
    const result = pendingAlgorithmChanges(next,evidence);
    expect(result.staleness.a?.kind).toBe('self_dirty');
    expect(result.staleness.b?.kind).toBe('upstream_dirty');
    expect(result.staleness.c).toBeNull();
    expect(result.diff[0].description).toContain('1.0.0 → demo@2.0.0');
    expect(evidence.staleness.a).toBeNull();
    expect(result.graph).toBe(evidence.graph);
  });
  it('preserves identity for aliases, parameter/cosmetic edits and reverting a pending replacement', () => {
    const next = structuredClone(graph);next.nodes[0].algorithm_name='kiri/demo';
    next.nodes[0].params={count:3};next.nodes[0].position.x=40;
    expect(pendingAlgorithmChanges(next,evidence)).toBe(evidence);
    pendingAlgorithmChanges(replaced(),evidence);
    expect(pendingAlgorithmChanges(graph,evidence)).toBe(evidence);
  });
  it('marks running evidence as old and preserves existing downstream reasons', () => {
    const baseline:ExecutionAnalysis={...evidence,canvas_jobs:[{job_id:'job',snapshot_id:'s',graph_node_id:'a',state:'running'} as any],
      staleness:{...evidence.staleness,b:{kind:'self_dirty',title:'参数已改'}}};
    const result=pendingAlgorithmChanges(replaced(),baseline);
    expect(result.staleness.a?.kind).toBe('inflight_old_params');
    expect(result.staleness.b).toBe(baseline.staleness.b);
    expect(result.canvas_jobs).toBe(baseline.canvas_jobs);
  });
  it('accepts fresh evidence unchanged and handles graph cycles', () => {
    const next=replaced();next.edges.push({id:'ba',source:'b',target:'a',sourceHandle:'out',targetHandle:'in'});
    expect(pendingAlgorithmChanges(next,evidence).staleness.b?.kind).toBe('upstream_dirty');
    const fresh={...evidence,graph:next,revision:'new'};
    expect(pendingAlgorithmChanges(next,fresh)).toBe(fresh);
  });
});
