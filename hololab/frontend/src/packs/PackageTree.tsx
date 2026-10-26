import {useEffect, useMemo, useState} from 'react';
import {buildTree, computeMatches, type TreePack, type TreeNode} from '../canvas/packTree';

export type PackageTreeItem = TreePack & {key: string; status?: string; problem?: boolean};
export function PackageTree({items, selected, query, onSelect, displayName, selectedCategory, onSelectCategory, disabled = false}: {
  displayName: (name: string) => string;
  selectedCategory: string; onSelectCategory: (path: string) => void;
  items: PackageTreeItem[]; selected: string; query: string; onSelect: (key: string) => void; disabled?: boolean;
}) {
  const tree = useMemo(() => buildTree(items, {includeView: false}), [items]);
  const matches = useMemo(() => computeMatches(tree, query), [tree, query]);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const selectedPath = selected ? treePath(tree, selected) : selectedCategory;
  // Reveal restored selections even if their ancestors were previously collapsed.
  useEffect(() => {
    if (!selectedPath) return;
    const segments = selectedPath.split('/');
    setOpen(prev => ({...prev, ...Object.fromEntries(segments.map((_, i) => [segments.slice(0, i + 1).join('/'), true]))}));
  }, [selectedPath]);
  const render = (node: TreeNode<PackageTreeItem>): React.ReactNode => {
    if (node.kind === 'view') return null;
    if (node.kind === 'leaf') {
      if (matches.active && !matches.matched.has(node.path)) return null;
      return <button key={node.path} className="hl-pack-leaf" disabled={disabled}
        aria-current={selected === node.pack.key ? 'page' : undefined} onClick={() => onSelect(node.pack.key)}>
        <span>{displayName(node.pack.name)}</span><small>{node.pack.version}</small>
        {node.pack.status && <small className="hl-pack-tree-status" data-problem={node.pack.problem}>{node.pack.status}</small>}
      </button>;
    }
    if (matches.active && !matches.forceOpen.has(node.path)) return null;
    const containsSelected=(child:TreeNode<PackageTreeItem>):boolean=>child.kind==='leaf'?child.pack.key===selected:child.kind==='folder'&&child.children.some(containsSelected);
    const expanded = matches.active || (open[node.path] ?? (node.depth === 0 || node.children.some(containsSelected)));
    return <div className="hl-pack-tree-group" key={node.path}>
      <div className="hl-local-folder-row">
        <button className="hl-local-folder-toggle" aria-label={`${expanded ? '收起' : '展开'} ${node.label}`} aria-expanded={expanded}
          onClick={() => setOpen(prev => ({...prev, [node.path]: !expanded}))}>{expanded ? '▾' : '▸'}</button>
        <button className="hl-pack-folder" aria-current={selectedCategory===node.path?'page':undefined}
          onClick={() => {onSelectCategory(node.path);setOpen(prev => ({...prev, [node.path]: true}));}}>{node.label}</button>
      </div>
      {expanded && <div className="hl-pack-tree-children">{node.children.map(render)}</div>}
    </div>;
  };
  return <>{tree.children.map(render)}</>;
}

function treePath(node: TreeNode<PackageTreeItem>, key: string): string {
  if (node.kind === 'leaf') return node.pack.key === key ? node.path : '';
  if (node.kind !== 'folder') return '';
  for (const child of node.children) {
    const path = treePath(child, key);
    if (path) return path;
  }
  return '';
}
