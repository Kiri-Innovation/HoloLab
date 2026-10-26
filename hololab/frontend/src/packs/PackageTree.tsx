import {useMemo, useState} from 'react';
import {buildTree, computeMatches, type TreePack, type TreeNode} from '../canvas/packTree';

export type PackageTreeItem = TreePack & {key: string; status?: string; problem?: boolean};
export function PackageTree({items, selected, query, onSelect, disabled = false}: {
  items: PackageTreeItem[]; selected: string; query: string; onSelect: (key: string) => void; disabled?: boolean;
}) {
  const tree = useMemo(() => buildTree(items, {includeView: false}), [items]);
  const matches = useMemo(() => computeMatches(tree, query), [tree, query]);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const render = (node: TreeNode<PackageTreeItem>): React.ReactNode => {
    if (node.kind === 'view') return null;
    if (node.kind === 'leaf') {
      if (matches.active && !matches.matched.has(node.path)) return null;
      return <button key={node.path} className="hl-pack-leaf" disabled={disabled}
        aria-current={selected === node.pack.key ? 'true' : undefined} onClick={() => onSelect(node.pack.key)}>
        <span>{node.pack.name}</span><small>{node.pack.version}</small>
        {node.pack.status && <small className="hl-pack-tree-status" data-problem={node.pack.problem}>{node.pack.status}</small>}
      </button>;
    }
    if (matches.active && !matches.forceOpen.has(node.path)) return null;
    const containsSelected=(child:TreeNode<PackageTreeItem>):boolean=>child.kind==='leaf'?child.pack.key===selected:child.kind==='folder'&&child.children.some(containsSelected);
    const expanded = matches.active || (open[node.path] ?? (node.depth === 0 || node.children.some(containsSelected)));
    return <div className="hl-pack-tree-group" key={node.path}>
      <button className="hl-pack-folder" aria-expanded={expanded} onClick={() => setOpen(prev => ({...prev, [node.path]: !expanded}))}>
        <span aria-hidden="true">{expanded ? '▾' : '▸'}</span> {node.label}
      </button>
      {expanded && <div className="hl-pack-tree-children">{node.children.map(render)}</div>}
    </div>;
  };
  return <>{tree.children.map(render)}</>;
}
