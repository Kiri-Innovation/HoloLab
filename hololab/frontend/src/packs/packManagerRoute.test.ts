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
