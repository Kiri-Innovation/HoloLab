import { ASSEMBLE } from "./assembly";
import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { CatalogPack } from "../wire";
import { buildTree, computeMatches, type TreeNode } from "./packTree";
import "./nodeCreationMenu.css";

export type NodeChoice = Exclude<TreeNode, { kind: "folder" }>;

export function NodeCreationMenu({ catalog, anchor, onCreate, onClose }: {
  catalog: CatalogPack[];
  anchor: { x: number; y: number };
  onCreate: (choice: NodeChoice) => void;
  onClose: () => void;
}) {
  const root = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const id = useId();
  const [query, setQuery] = useState("");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState("");
  const [position, setPosition] = useState(anchor);
  const tree = useMemo(() => buildTree(catalog), [catalog]);
  const matches = useMemo(() => computeMatches(tree, query), [tree, query]);
  const rows: TreeNode[] = [];
  const walk = (node: TreeNode) => {
    if (matches.active && !(node.kind === "folder" ? matches.forceOpen : matches.matched).has(node.path)) return;
    rows.push(node);
    if (node.kind === "folder" && !collapsed.has(node.path)) node.children.forEach(walk);
  };
  tree.children.forEach(walk);
  const choices = rows.filter((node): node is NodeChoice => node.kind !== "folder");
  const active = choices.find(node => node.path === selected) ?? choices[0];
  const activeIndex = choices.indexOf(active);

  useLayoutEffect(() => {
    const rect = root.current!.getBoundingClientRect();
    setPosition({
      x: Math.max(8, Math.min(anchor.x, window.innerWidth - rect.width - 8)),
      y: Math.max(8, Math.min(anchor.y, window.innerHeight - rect.height - 8)),
    });
    input.current?.focus();
  }, [anchor]);

  useEffect(() => {
    const outside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) onClose();
    };
    document.addEventListener("pointerdown", outside, true);
    window.addEventListener("resize", onClose);
    return () => {
      document.removeEventListener("pointerdown", outside, true);
      window.removeEventListener("resize", onClose);
    };
  }, [onClose]);

  useEffect(() => {
    root.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
  }, [active?.path]);

  return createPortal(
    <div ref={root} className="hl-node-creation-menu" role="dialog" aria-label="创建节点"
      style={{ left: position.x, top: position.y }}
      onContextMenu={event => event.preventDefault()}
      onKeyDown={event => {
        event.stopPropagation();
        if (event.nativeEvent.isComposing) return;
        if (event.key === "Escape") { event.preventDefault(); onClose(); }
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          if (choices.length) setSelected(choices[(activeIndex + (event.key === "ArrowDown" ? 1 : -1) + choices.length) % choices.length].path);
        }
        if (event.key === "Enter" && event.target === input.current) {
          event.preventDefault();
          if (active) onCreate(active);
        }
      }}
      onKeyUp={event => event.stopPropagation()}>
      <input ref={input} type="search" aria-label="搜索节点" placeholder="搜索节点或 Pack…"
        role="combobox" aria-expanded="true" aria-controls={id} aria-autocomplete="list"
        aria-activedescendant={active ? `${id}-${activeIndex}` : undefined}
        value={query} onChange={event => {
          setQuery(event.target.value);
          setSelected("");
          setCollapsed(new Set());
        }} />
      <div className="hl-node-creation-list" id={id} role="listbox" aria-label="节点类型">
        {rows.map(node => node.kind === "folder" ? (
          <div role="presentation" key={node.path}>
            <button className="hl-node-creation-folder" aria-expanded={!collapsed.has(node.path)}
              style={{ paddingLeft: 8 + node.depth * 12 }}
              onClick={() => setCollapsed(previous => {
                const next = new Set(previous);
                if (next.has(node.path)) next.delete(node.path); else next.add(node.path);
                return next;
              })}>
              <span aria-hidden>{collapsed.has(node.path) ? "▸" : "▾"}</span> {node.label}
            </button>
          </div>
        ) : (
          <div role="option" id={`${id}-${choices.indexOf(node)}`} key={node.path}
            aria-selected={node === active} className="hl-node-creation-option"
            style={{ paddingLeft: 20 + node.depth * 12 }}
            onMouseMove={() => setSelected(node.path)} onClick={() => onCreate(node)}
            title={node.kind === "leaf" ? node.pack.description ?? undefined : "Preview an input artifact"}>
            <span>{node.kind === "leaf" ? (node.pack.name === ASSEMBLE ? "array-assemble" : node.pack.name) : node.label}</span>
            {node.kind === "leaf" && <small>v{node.pack.version}</small>}
          </div>
        ))}
        {matches.active && matches.matched.size === 0 && <div role="status" className="hl-node-creation-empty">无匹配</div>}
      </div>
      <div className="hl-node-creation-hint">↑↓ 选择 · Enter 创建 · Esc 关闭</div>
    </div>, document.body,
  );
}
