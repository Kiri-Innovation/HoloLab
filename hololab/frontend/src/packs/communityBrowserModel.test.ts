import {describe,expect,it} from 'vitest';
import {browseProjects,latestRelease,projectCategory,type CommunityRelease,type CommunityProject,type CommunityFilter} from './communityBrowserModel';
const release=(name:string,date:string,category='reconstruction/colmap',publisher='Alice'):CommunityRelease=>({name,version:'1',published_at:date,publisher,yanked:false,code_sha256:'hash',env:'',requires:null,category});
const filter:CommunityFilter={search:'',category:'',publisher:'',scope:'',installed:'all'};
const a=release('kiri/a','2026-10-26T09:00:00+08:00');
const b=release('demo/b','2026-10-26T02:00:00Z');
const c=release('demo/c','2026-10-25T23:00:00Z','image/filter','Bob');
const projects:CommunityProject[]=[{name:a.name,releases:[a]},{name:c.name,releases:[c]},{name:b.name,releases:[b]}];
const browse=(changes:Partial<CommunityFilter>={})=>browseProjects(projects,{...filter,...changes},r=>r.name===a.name).map(p=>p.name);
describe('community browser',()=>{
 it('sorts across projects by actual latest time, including timezone offsets',()=>{
  expect(browse()).toEqual(['demo/b','kiri/a','demo/c']);
  expect(latestRelease({name:'x',releases:[a,b,c]})).toBe(b);
 });
 it('puts unknown dates last and resolves equal times deterministically',()=>{
  expect(browseProjects([{name:'z',releases:[b]},{name:'a',releases:[b]},{name:'unavailable',error:'offline'}],filter,()=>false).map(p=>p.name)).toEqual(['a','z','unavailable']);
  expect(projectCategory({name:'unknown'})).toEqual(['misc']);
 });
 it('combines search, parent category, publisher, scope and installed filters',()=>{
  expect(browse({category:'reconstruction'})).toEqual(['demo/b','kiri/a']);
  expect(browse({category:'reconstruct'})).toEqual([]);
  expect(browse({search:'ALICE',scope:'kiri',installed:'installed'})).toEqual(['kiri/a']);
  expect(browse({publisher:'Bob'})).toEqual(['demo/c']);
  expect(browse({installed:'uninstalled'})).toEqual(['demo/b','demo/c']);
 });
 it('does not classify unreadable metadata as confirmed uninstalled',()=>{
  expect(browseProjects([{name:'unknown'}],{...filter,installed:'uninstalled'},()=>false)).toEqual([]);
 });
});
