import {describe, expect, it} from 'vitest';
import {buildTree, computeMatches} from './packTree';

const packs = [
  {name:'kiri/sfm',version:'1',category:['reconstruction','colmap']},
  {name:'another/filter',version:'1',category:['image','filter']},
  {name:'kiri/uncategorized',version:'1',category:[]},
];
describe('shared category tree',()=>{
  it('retains editor category nesting, ordering, misc fallback and visualize leaf',()=>{
    const tree=buildTree(packs);
    expect(tree.children.map(n=>n.kind==='folder'?n.label:'')).toEqual(['image','misc','reconstruction']);
    const misc=tree.children.find(n=>n.kind==='folder'&&n.label==='misc');
    expect(misc?.kind==='folder' && misc.children.map(n=>n.kind)).toEqual(['leaf','view']);
    expect(computeMatches(tree,'colmap').forceOpen).toEqual(new Set(['reconstruction','reconstruction/colmap']));
    expect(computeMatches(tree,'sfm').matched).toEqual(new Set(['reconstruction/colmap/kiri/sfm@1']));
  });
  it('manager omits only the editor-only visualize item, keeping the same package hierarchy',()=>{
    const manager=buildTree(packs,{includeView:false});
    const editor=buildTree(packs);
    const misc=editor.children.find(n=>n.kind==='folder'&&n.label==='misc');
    if(misc?.kind==='folder')misc.children=misc.children.filter(n=>n.kind!=='view');
    expect(manager).toEqual(editor);
    expect(buildTree([],{includeView:false}).children).toEqual([]);
  });
});
