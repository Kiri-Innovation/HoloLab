import {describe, expect, it} from 'vitest';
import {buildPackEntries, installedOn, installCommand, packHasProblem} from './packManagerModel';
import type {CatalogPack, WorkflowGraph} from '../wire';

const catalog = [{name:'train',version:'1',node_ids:['gpu'],requires:null}] as CatalogPack[];
const graph = {nodes:[{id:'a',algorithm_name:'kiri/train',algorithm_version:'1',params:{}},
  {id:'b',algorithm_name:'other/missing',algorithm_version:'2',params:{}}, {id:'view',kind:'view'}],edges:[]} as unknown as WorkflowGraph;
const workflows = [{workflow_id:'w',name:'Workflow',graph,check:{issues:[{node_id:'a',code:'environment',message:'环境未声明'}]}}];
describe('package-oriented aggregation',()=>{
  it('merges aliases, keeps missing references and excludes view nodes',()=>{
    const entries=buildPackEntries(catalog,[],workflows);
    expect(entries).toHaveLength(2);
    expect(entries[0].name).toBe('kiri/train');
    expect(entries[0].usages[0].node.id).toBe('a');
    expect(entries[1].scope).toBe('other');
    expect(packHasProblem(entries[1])).toBe(true);
    expect(installedOn(entries[0],'gpu')).toBe(true);
    expect(installedOn(entries[0],'other')).toBe(false);
  });
  it('does not classify undeclared requirements as a problem',()=>{
    const [entry]=buildPackEntries(catalog,[],workflows);
    expect(packHasProblem(entry,{status:'undeclared',issues:[]})).toBe(false);
    expect(packHasProblem(entry,{status:'missing',issues:[]})).toBe(true);
    expect(packHasProblem(entry,{status:'unknown',issues:[]})).toBe(true);
    expect(packHasProblem(entry,undefined,'yanked')).toBe(true);
  });
  it('records every referencing node without treating offline receipts as current installs',()=>{
    const entries=buildPackEntries([], [{name:'train',version:'1',node_id:'gpu',online:false}],
      [...workflows,{...workflows[0],workflow_id:'w2'}]);
    expect(entries[0].usages).toHaveLength(2);
    expect(installedOn(entries[0],'gpu')).toBe(false);
  });
  it('includes registry-only versions without inventing local installation',()=>{
    const [entry]=buildPackEntries([],[],[],[{name:'scope/pkg',version:'1',yanked:false,source:'https://registry.test'}]);
    expect(entry.catalog).toBeUndefined();
    expect(entry.release?.source).toBe('https://registry.test');
    expect(packHasProblem(entry)).toBe(false);
  });
  it('quotes installation arguments instead of treating package/source text as shell',()=>{
    expect(installCommand('scope/pkg','1','file:///registry')).toBe("hololab pack install 'scope/pkg@1' --source 'file:///registry'");
    expect(installCommand('x\'$(touch /tmp/x)','1')).toBe("hololab pack install 'x'\\''$(touch /tmp/x)@1'");
  });
});
