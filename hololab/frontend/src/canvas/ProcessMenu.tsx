import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { writeToClipboard } from "./referenceAction";
import { copyProcessReference, type ProcessReference } from "./processReference";

export function ProcessMenu({ process, allowTerminate = true, canTerminate, onTerminate, onMessage }: {
  process: ProcessReference; allowTerminate?: boolean; canTerminate: boolean; onTerminate: () => void; onMessage: (text: string) => void;
}) {
  const [position, setPosition] = useState<{left: number; top: number} | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const id = useId();
  const close = () => { setPosition(null); trigger.current?.focus(); };
  useEffect(() => {
    if (!position) return;
    menu.current?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus();
    const outside = (e: PointerEvent) => { if (!menu.current?.contains(e.target as Node) && !trigger.current?.contains(e.target as Node)) setPosition(null); };
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); e.stopImmediatePropagation(); close(); }
      if (["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) {
        e.preventDefault();
        const items = Array.from(menu.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') ?? []);
        const i = items.indexOf(document.activeElement as HTMLButtonElement);
        items[e.key === "Home" ? 0 : e.key === "End" ? items.length-1 : (i + (e.key === "ArrowUp" ? -1 : 1) + items.length) % items.length]?.focus();
      }
      if (e.key === "Tab") { e.preventDefault(); close(); }
    };
    const dismiss = () => setPosition(null);
    window.addEventListener("pointerdown", outside);
    window.addEventListener("keydown", key, true);
    window.addEventListener("resize", dismiss);
    window.addEventListener("scroll", dismiss, true);
    return () => { window.removeEventListener("pointerdown", outside); window.removeEventListener("keydown", key, true); window.removeEventListener("resize", dismiss); window.removeEventListener("scroll", dismiss, true); };
  }, [position]);
  return <>
    <button ref={trigger} className="lens-kebab" aria-label={`进程菜单 · PID ${process.pid}`} aria-haspopup="menu" aria-expanded={!!position} aria-controls={position ? id : undefined} onClick={() => {
      if (position) { close(); return; }
      const rect = trigger.current!.getBoundingClientRect();
      setPosition({ left: Math.max(8, Math.min(rect.right - 160, window.innerWidth - 168)), top: Math.max(8, Math.min(rect.bottom + 4, window.innerHeight - 132)) });
    }}>⋮</button>
    {position && createPortal(<div ref={menu} id={id} role="menu" aria-label={`进程操作 · PID ${process.pid}`} className="hl-process-menu" style={{left:position.left, top:position.top}}>
      {allowTerminate && <button role="menuitem" className="process-menu-danger" disabled={!canTerminate} onClick={() => { close(); onTerminate(); }}>结束进程</button>}
      <button role="menuitem" onClick={async () => { close(); onMessage(await writeToClipboard(String(process.pid)) ? "已复制 PID" : "复制 PID 失败，请重试"); }}>复制 PID</button>
      <button role="menuitem" onClick={async () => { close(); onMessage((await copyProcessReference(process)).message); }}>复制引用</button>
    </div>, document.body)}
  </>;
}
