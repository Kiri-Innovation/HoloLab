import {useEffect, useMemo, useState} from 'react';
import {buildTree, computeMatches, type TreePack, type TreeNode} from '../canvas/packTree';

export type PackageTreeItem = TreePack & {key: string; status?: string; problem?: boolean};
export function collapseRedundantCategory<T extends TreePack>(item: T): T {
  const name = item.name.split('/').pop();
  const category = item.category ?? [];
  return name && category.at(-1) === name ? {...item, category: category.slice(0, -1)} : item;
}
function collapsedCategoryPath(items: PackageTreeItem[], path: string) {
  return items.some(item => item.category.join('/') === path && collapseRedundantCategory(item).category.join('/') !== path)
    ? path.split('/').slice(0, -1).join('/') : path;
}
export function PackageTree({items, selected, query, onSelect, displayName, selectedCategory, onSelectCategory, collapseRedundantCategories = false, disabled = false}: {
  displayName: (name: string) => string;
  selectedCategory: string; onSelectCategory?: (path: string) => void;
  items: PackageTreeItem[]; selected: string; query: string; onSelect: (key: string) => void; collapseRedundantCategories?: boolean; disabled?: boolean;
}) {
  const treeItems = useMemo(() => collapseRedundantCategories ? items.map(collapseRedundantCategory) : items, [items, collapseRedundantCategories]);
  const tree = useMemo(() => buildTree(treeItems, {includeView: false}), [treeItems]);
  const matches = useMemo(() => computeMatches(tree, query), [tree, query]);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const visibleCategory = collapseRedundantCategories ? collapsedCategoryPath(items, selectedCategory) : selectedCategory;
  const selectedPath = selected ? treePath(tree, selected) : visibleCategory;
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
        <svg className="hl-pack-algorithm-icon" viewBox="0 0 16 16" aria-hidden="true"><path d="m8 1.75 5 2.75v5.75L8 13.5l-5-2.75V5.5L8 2.75l5 2.75M8 8.25v5.25m5-9-5 2.75L3 4.5" /></svg>
        <span className="hl-pack-leaf-content"><span className="hl-pack-leaf-heading"><span className="hl-pack-leaf-name">{displayName(node.pack.name)}</span><small className="hl-pack-leaf-version">{node.pack.version}</small></span>
          {node.pack.status && <small className="hl-pack-tree-status" data-problem={node.pack.problem}><svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="5.5" /><path d="M8 7v3m0-5v.01" /></svg><span>{node.pack.status}</span></small>}
        </span>
      </button>;
    }
    if (matches.active && !matches.forceOpen.has(node.path)) return null;
    const containsSelected=(child:TreeNode<PackageTreeItem>):boolean=>child.kind==='leaf'?child.pack.key===selected:child.kind==='folder'&&child.children.some(containsSelected);
    const expanded = matches.active || (open[node.path] ?? (node.depth === 0 || node.children.some(containsSelected)));
    return <div className="hl-pack-tree-group" key={node.path}>
      <div className="hl-local-folder-row">
        <button className="hl-local-folder-toggle" aria-label={`${expanded ? '收起' : '展开'} ${node.label}`} aria-expanded={expanded}
          onClick={() => setOpen(prev => ({...prev, [node.path]: !expanded}))}><svg className="hl-tree-chevron" viewBox="0 0 16 16" aria-hidden="true"><path d="m6 3 5 5-5 5" /></svg></button>
        <button className="hl-pack-folder" aria-current={onSelectCategory && visibleCategory===node.path?'page':undefined}
          onClick={() => onSelectCategory
            ? (onSelectCategory(node.path), setOpen(prev => ({...prev, [node.path]: true})))
            : setOpen(prev => ({...prev, [node.path]: !(prev[node.path] ?? true)}))}>{node.label}</button>
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
