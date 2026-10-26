import {describe, expect, it} from 'vitest';
import {buildPackEntries, installedOn, installCommand, packHasProblem, packProblemReasons, packEnvironmentStatus} from './packManagerModel';
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
    expect(packHasProblem(entry,{status:'unknown',issues:[]})).toBe(false);
    expect(packHasProblem(entry,undefined,'yanked')).toBe(true);
  });
  it('keeps unknown local and workflow environments informational',()=>{
    const [entry]=buildPackEntries(catalog,[],workflows);
    entry.usages[0].workflow={...entry.usages[0].workflow,check:{issues:[],environment:{a:{status:'unknown',issues:[]}}}};
    expect(packHasProblem(entry,{status:'unknown',issues:[]})).toBe(false);
    expect(packEnvironmentStatus(entry,{status:'unknown',issues:[]})).toBe('环境未校验');
    expect(packEnvironmentStatus(entry,{status:'satisfied',issues:[]})).toBe('工作流运行节点环境未校验');
  });
  it('retains concrete reasons for missing dependencies, recipes, yank and identity',()=>{
    const [entry,absent]=buildPackEntries(catalog,[],workflows);
    const missing={status:'missing' as const,issues:[{item:'mmcv',required:'*',actual:null,remedy:'install'}]};
    expect(packProblemReasons(entry,missing)).toEqual(['缺少环境：mmcv']);
    expect(packProblemReasons(absent)).toEqual(['1 条工作流引用但包未安装']);
    expect(packProblemReasons(entry,undefined,'yanked')).toEqual(['版本已 yank，不能新安装']);
    expect(packProblemReasons(entry,undefined,'hash_mismatch')).toEqual(['代码哈希不匹配']);
    entry.usages[0].issues=[{node_id:'a',code:'missing_parameter',message:'参数 frames 未提供值'}];
    entry.usages[0].workflow={...entry.usages[0].workflow,checkFailed:true,check:{issues:[],environment:{a:missing}}};
    expect(packProblemReasons(entry)).toEqual(['参数 frames 未提供值','工作流配方检查未完成，请重试','工作流运行节点缺少环境：mmcv']);
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
