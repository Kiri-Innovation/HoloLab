import {expect,it} from 'vitest';
import {packManagerPath,packManagerRoute} from './packManagerRoute';
it('defaults bare entry and tab URLs to landing views',()=>{
 expect(packManagerRoute('').localSection).toBe('overview');
 expect(packManagerRoute('?view=community').communitySection).toBe('home');
 expect(packManagerRoute('?view=local&section=unknown').localSection).toBe('overview');
});
it('round-trips view selection and index parameters for reload/history',()=>{
 const path=packManagerPath('community','category',{category:'reconstruction/colmap'});
 expect(packManagerRoute(path.slice(path.indexOf('?')))).toMatchObject({view:'community',communitySection:'category',category:'reconstruction/colmap'});
 const local=packManagerPath('local','category',{pack:'kiri/sfm@0.4.1'});
 expect(packManagerRoute(local.slice(local.indexOf('?')))).toMatchObject({localSection:'category',pack:'kiri/sfm@0.4.1'});
 expect(packManagerRoute('?view=community&section=installed').communitySection).toBe('installed');
});
it('persists the local tree grouping in the URL',()=>{
 const scope=packManagerPath('local','category',{pack:'kiri/sfm@0.4.1',tree:'scope'});
 expect(packManagerRoute(scope.slice(scope.indexOf('?')))).toMatchObject({localSection:'category',pack:'kiri/sfm@0.4.1',tree:'scope'});
 expect(packManagerRoute('?view=local&section=overview').tree).toBe('category');
});
it('round-trips local categories independently from package and status selection',()=>{
 const category=packManagerPath('local','category',{category:'reconstruction/colmap'});
 expect(packManagerRoute(category.slice(category.indexOf('?')))).toMatchObject({localSection:'category',category:'reconstruction/colmap',pack:''});
 const missing=packManagerPath('local','missing');
 expect(packManagerRoute(missing.slice(missing.indexOf('?')))).toMatchObject({localSection:'missing',category:'',pack:''});
});

it('resolves unscoped local deep links with the same identity as catalog entries',()=>{
 const raw=packManagerRoute('?view=local&section=category&pack=megasam-track%400.4.0');
 const scoped=packManagerRoute('?view=local&section=category&pack=kiri%2Fmegasam-track%400.4.0');
 expect(raw.pack).toBe('kiri/megasam-track@0.4.0');
 expect(raw).toEqual(scoped);
 expect(packManagerRoute('?pack=megasam-track%400.4.0').pack).toBe(raw.pack);
 for (const key of ['col4d/merge-colmap4d@0.1.5','col4d/merge-colmap4d@0.2.0','community/birefnet-mask@0.1.2']) {
  expect(packManagerRoute('?view=local&section=category&pack='+encodeURIComponent(key)).pack).toBe(key);
 }
});
it('does not rewrite community identities or malformed references',()=>{
 expect(packManagerRoute('?view=community&pack=megasam-track%400.4.0').pack).toBe('megasam-track@0.4.0');
 for (const key of ['', 'megasam-track', '@0.4.0','megasam-track@']) {
  expect(packManagerRoute('?view=local&pack='+encodeURIComponent(key)).pack).toBe(key);
 }
});
