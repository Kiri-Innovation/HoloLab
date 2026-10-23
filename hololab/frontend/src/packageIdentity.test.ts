import { expect, it } from 'vitest';
import { canonicalPackageName, packageCatalogIndex, packAssetUrl, samePackage } from './packageIdentity';
import { effectiveParameterPins } from './canvas/parameterPins';
import { arrayParamsForEditor } from './canvas/arrayMigration';
it('compares only the fixed legacy scope and preserves intrinsic identities', () => {
  expect(samePackage('foo','1.0.0','kiri/foo','1.0.0')).toBe(true);
  expect(samePackage('foo','1.0.0','team/foo','1.0.0')).toBe(false);
  expect(samePackage('foo','1.0.0+','foo','1.0.0')).toBe(false);
  expect(canonicalPackageName('@builtin/array-assemble')).toBe('@builtin/array-assemble');
});
it('preserves recorded spelling even after a catalog refresh', () => {
  const pack={name:'kiri/foo',version:'1.0.0'};
  const index=packageCatalogIndex([pack]);
  expect(index.get('foo@1.0.0')?.name).toBe('foo');
  expect(index.get('kiri/foo@1.0.0')?.name).toBe('kiri/foo');
  expect(index.has('team/foo@1.0.0')).toBe(false);
  expect(pack.name).toBe('kiri/foo');
});
it('keeps explicit empty legacy pins and scopes array migration', () => {
  const pack={name:'kiri/foo',version:'1.0.0',params:{x:{pinned:true}}};
  expect(effectiveParameterPins({'foo@1.0.0':[]},pack)).toEqual([]);
  expect(effectiveParameterPins({'foo@1.0.0':['x'],'kiri/foo@1.0.0':[]},pack)).toEqual([]);
  const data={items:'[{"offset":2}]'};
  expect(arrayParamsForEditor('kiri/float-array-source','0.1.0',data).items).toEqual([2]);
  expect(arrayParamsForEditor('team/float-array-source','0.1.0',data)).toEqual(data);
});
it('uses separate path segments and encodes dev versions without changing old URLs', () => {
  expect(packAssetUrl('n','scope/example','0.1.1+','viewer/index.html')).toBe('/scoped-pack-assets/n/scope/example/0.1.1%2B/viewer/index.html');
  expect(packAssetUrl('n','foo','0.1.0','viewer/index.html')).toBe('/pack-assets/n/foo/0.1.0/viewer/index.html');
});
