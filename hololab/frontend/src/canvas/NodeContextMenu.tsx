import { useEffect, useId, useRef } from "react";
import { createPortal } from "react-dom";

export type NodeMenuPosition = { x: number; y: number };

export function NodeContextMenu({
  position,
  canRun,
  running,
  hasPack,
  onClose,
  onDelete,
  onDuplicate,
  onRun,
  packHref,
}: {
  position: NodeMenuPosition;
  canRun: boolean;
  running: boolean;
  hasPack: boolean;
  onClose: () => void;
  onDelete: () => void;
  onDuplicate: () => void;
  onRun?: () => void;
  packHref?: string;
}) {
  const menu = useRef<HTMLDivElement>(null);
  const priorFocus = useRef<HTMLElement | null>(document.activeElement instanceof HTMLElement ? document.activeElement : null);
  const pointerStartedOutside = useRef(false);
  const id = useId();
  const left = Math.max(8, Math.min(position.x, window.innerWidth - 208));
  const top = Math.max(8, Math.min(position.y, window.innerHeight - 236));

  useEffect(() => {
    menu.current?.querySelector<HTMLElement>("button:not(:disabled), a[role=menuitem]")?.focus();
    const outsideDown = (event: PointerEvent) => {
      pointerStartedOutside.current = !menu.current?.contains(event.target as Node);
    };
    const outsideUp = (event: PointerEvent) => {
      if (pointerStartedOutside.current && !menu.current?.contains(event.target as Node)) onClose();
      pointerStartedOutside.current = false;
    };
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); event.stopImmediatePropagation(); onClose(); return; }
      if (event.key === "Tab") { event.preventDefault(); onClose(); return; }
      if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const items = Array.from(menu.current?.querySelectorAll<HTMLElement>("button:not(:disabled), a[role=menuitem]") ?? []);
      const current = items.indexOf(document.activeElement as HTMLElement);
      const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : (current + (event.key === "ArrowUp" ? -1 : 1) + items.length) % items.length;
      items[next]?.focus();
    };
    const dismiss = () => onClose();
    window.addEventListener("pointerdown", outsideDown, true);
    window.addEventListener("pointerup", outsideUp, true);
    window.addEventListener("keydown", key, true);
    window.addEventListener("resize", dismiss);
    window.addEventListener("scroll", dismiss, true);
    return () => {
      window.removeEventListener("pointerdown", outsideDown, true);
      window.removeEventListener("pointerup", outsideUp, true);
      window.removeEventListener("keydown", key, true);
      window.removeEventListener("resize", dismiss);
      window.removeEventListener("scroll", dismiss, true);
      priorFocus.current?.focus();
    };
  }, [onClose]);

  const action = (callback: () => void) => () => { onClose(); callback(); };
  return createPortal(
    <div ref={menu} id={id} role="menu" aria-label="节点操作" className="hl-node-context-menu" style={{ left, top }} onContextMenu={event => event.preventDefault()}>
      <button role="menuitem" type="button" onClick={action(onDuplicate)}>创建副本</button>
      {onRun && <button role="menuitem" type="button" disabled={!canRun} title={hasPack ? undefined : "算法未安装，无法运行"} onClick={action(onRun)}>{running ? "停止运行" : "运行"}</button>}
      {packHref && <a role="menuitem" href={packHref} target="_blank" rel="noopener" onClick={onClose}>在包管理器里查看</a>}
      <div role="separator" className="hl-node-context-menu-separator" />
      <button role="menuitem" type="button" className="hl-node-context-menu-danger" onClick={action(onDelete)}>删除</button>
    </div>,
    document.body,
  );
}
