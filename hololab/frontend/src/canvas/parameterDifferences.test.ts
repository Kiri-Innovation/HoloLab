import { expect, it } from 'vitest';
import type { CatalogPack } from '../wire';
import { parameterDifferences, parameterDifferenceText, sameParameterValue } from './parameterDifferences';
const pack = { name: 'test', version: '1', params: { count: { type: 'int', default: 12, description: null, optional: false }, path: { type: 'path', default: '/data', description: null, optional: false } }, manifest_hash: 'test', node_ids: [], description: null, category: [], docs: null, source_entry: null, inputs: {}, outputs: {} } as CatalogPack;
it('is silent for matching defaults and absent analysis', () => {
  expect(parameterDifferences(pack, {}, {count:12,path:'/data'})).toEqual([]);
  expect(parameterDifferences(pack, {}, undefined)).toEqual([]);
});
it('reports real normalized paths without changing the editor value', () => {
  const draft = {count:12,path:'~/data'};
  const diffs = parameterDifferences(pack,draft,{count:12,path:'/home/user/data'},['path']);
  expect(diffs).toHaveLength(1);
  expect(parameterDifferenceText(diffs[0])).toBe('path：显示 ~/data / 实际用 /home/user/data / 环境相关路径规范化');
  expect(draft.path).toBe('~/data');
});
it('requires explicit provenance for future variables and history', () => {
  const variable = parameterDifferences(pack,{count:'iterations',path:'/data'},{count:12000,path:'/data'},[],{count:'workflow-variable'});
  expect(parameterDifferenceText(variable[0])).toContain('显示 iterations / 实际用 12000 / 工作流变量解析');
  const history = parameterDifferences(pack,{}, {count:10,path:'/data'},[],{count:'history-missing'});
  expect(parameterDifferenceText(history[0])).toContain('显示 历史未记录 / 实际用 10 / 历史未记录');
  expect(parameterDifferences(pack,{}, {path:'/data'},[],{count:'history-missing'})).toEqual([]);
});
it('does not invent a variable or path reason', () => {
  expect(parameterDifferences(pack,{count:1},{count:2,path:'/data'})[0].reason).toBe('resolved-value');
});
it('ignores object key order but distinguishes null, empties, types and array order', () => {
  expect(sameParameterValue({a:1,b:[2,null]},{b:[2,null],a:1})).toBe(true);
  for (const [a,b] of [[null,undefined],[null,''],[[],''],[1,'1'],[[1,2],[2,1]]]) expect(sameParameterValue(a,b)).toBe(false);
  expect(parameterDifferences(pack,{count:null,path:''},{count:null,path:''})).toEqual([]);
});
