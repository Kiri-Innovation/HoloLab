import {useMemo, useState} from 'react';
import {buildTree, type TreeNode, type TreePack} from '../canvas/packTree';

export function CategoryIndex({items, selected, onSelect}:{items:TreePack[]; selected:string; onSelect:(path:string)=>void}) {
  const tree=useMemo(()=>buildTree(items,{includeView:false}),[items]);
  const [open,setOpen]=useState<Record<string,boolean>>({});
  const render=(node:TreeNode<TreePack>):React.ReactNode=>{
    if(node.kind!=='folder')return null;
    const folders=node.children.filter(child=>child.kind==='folder');
    const expanded=open[node.path] ?? node.depth===0;
    return <div key={node.path}>
      <div className="hl-community-category-row">
        {folders.length>0 && <button aria-label={`${expanded?'收起':'展开'} ${node.label}`} aria-expanded={expanded} onClick={()=>setOpen(prev=>({...prev,[node.path]:!expanded}))}>{expanded?'▾':'▸'}</button>}
        <button aria-current={selected===node.path?'true':undefined} onClick={()=>{onSelect(node.path);setOpen(prev=>({...prev,[node.path]:true}));}}>{node.label}</button>
      </div>
      {expanded && folders.length>0 && <div className="hl-pack-tree-children">{folders.map(render)}</div>}
    </div>;
  };
  return <nav aria-label="功能分类">{tree.children.map(render)}</nav>;
}
