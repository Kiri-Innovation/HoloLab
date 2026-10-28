import { expect, it } from 'vitest';
import { ASSEMBLE, newAssembly, assemblyPresentation } from './assembly';
import { validateConnection } from './connectionValidation';
import { effectiveOutputType, type EdgeTypeCtx } from './edgeLabels';
import { computeStaleness } from './staleness';
import type { CatalogPack, GraphNode, WorkflowGraph } from '../wire';

function context(): EdgeTypeCtx {
  const nodes: GraphNode[] = ['video','image','group','consumer'].map(id => ({id,algorithm_name:id,algorithm_version:'1',position:{x:0,y:0},params:{},assigned_node_id:'worker'}));
  Object.assign(nodes[2],{kind:'intrinsic',algorithm_name:ASSEMBLE,operation:'array-assemble',assembly:newAssembly()});
  const packs = ['video','image'].map(name => ({name,version:'1',inputs:{},outputs:{out:{tags:[name === 'video' ? 'video-source':'image'],arrayed:false}}}) as unknown as CatalogPack);
  packs.push(assemblyPresentation());
  return {nodes,edges:[],catalogByKey:new Map(packs.map(p=>[`${p.name}@${p.version}`,p]))};
}
it('binds concrete T, rejects image after video, preserves ordinary rule',()=>{
 const ctx=context();const video={source:'video',sourceHandle:'out',target:'group',targetHandle:'slot-0'};
 expect(validateConnection(video,ctx).ok).toBe(true);
 ctx.edges = [{id:'v',...video}];
 expect(validateConnection({...video,source:'image',targetHandle:'slot-1'},ctx).reason).toContain('严格同型');
 expect(validateConnection({...video,targetHandle:'slot-1'},ctx).ok).toBe(true);
 expect(validateConnection(video,ctx).reason).toBe('这两个端口已经连接。');
 ctx.nodes = [...ctx.nodes, {...ctx.nodes[0],id:'other-video'}];
 expect(validateConnection({...video,source:'other-video'},ctx).ok).toBe(true);
 expect(validateConnection({...video,source:'image'},ctx).reason).toContain('严格同型');
 expect(effectiveOutputType('group','out',ctx)).toMatchObject({tags:['video-source'],arrayed:true,dimLabels:['item']});
});
it('slot order and empty-slot edits mark downstream stale, positions do not',()=>{
 const ctx=context();const snap:WorkflowGraph={nodes:[...ctx.nodes],edges:[{id:'e',source:'group',sourceHandle:'out',target:'consumer',targetHandle:'in'}]};
 const draft=structuredClone(snap);draft.nodes[2].assembly!.slots.reverse();
 const runtime={group:{state:'done'},consumer:{state:'done'}};
 const stale=computeStaleness(draft,snap,runtime);
 expect(stale.group).not.toBeNull();expect(stale.consumer).not.toBeNull();
 draft.nodes[2].assembly=structuredClone(snap.nodes[2].assembly);draft.nodes[2].position={x:999,y:999};
 expect(computeStaleness(draft,snap,runtime).group).toBeNull();
 draft.nodes[2].assembly!.slots.push('third');
 expect(computeStaleness(draft,snap,runtime).group).not.toBeNull();
});
