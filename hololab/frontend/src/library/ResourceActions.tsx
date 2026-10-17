import { useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { insertOrCopyReference } from '../canvas/referenceAction';
import { showReferenceResult } from '../canvas/CopyRefButton';
import { libraryReference } from './libraryReference';
import type { Resource } from './api';

export default function ResourceActions({ resource, path, onDelete }: {
  resource: Resource; path: (id: string) => string; onDelete: () => void;
}) {
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const id = useId();
  const zh = document.documentElement.lang.toLowerCase().startsWith('zh') || navigator.language.toLowerCase().startsWith('zh');
  const close = () => { setPosition(null); trigger.current?.focus(); };
  // Keep resource operations declarative so future actions can join this menu.
  const actions = [{ id: 'delete', label: resource.status === 'deleting' ? (zh ? '重试删除' : 'Retry deletion') : (zh ? '删除资源' : 'Delete resource'), danger: true, run: onDelete }];
  // Same portal/focus/dismissal pattern as ProcessMenu; Library owns the styling.
  useEffect(() => {
    if (!position) return;
    menu.current?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus();
    const outside = (event: PointerEvent) => {
      if (!menu.current?.contains(event.target as Node) && !trigger.current?.contains(event.target as Node)) setPosition(null);
    };
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopImmediatePropagation(); close(); }
      if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
        event.preventDefault();
        const items = Array.from(menu.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') ?? []);
        const index = items.indexOf(document.activeElement as HTMLButtonElement);
        items[event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : (index + (event.key === 'ArrowUp' ? -1 : 1) + items.length) % items.length]?.focus();
      }
      if (event.key === 'Tab') { event.preventDefault(); close(); }
    };
    const dismiss = () => setPosition(null);
    window.addEventListener('pointerdown', outside);
    window.addEventListener('keydown', key, true);
    window.addEventListener('resize', dismiss);
    window.addEventListener('scroll', dismiss, true);
    return () => {
      window.removeEventListener('pointerdown', outside); window.removeEventListener('keydown', key, true);
      window.removeEventListener('resize', dismiss); window.removeEventListener('scroll', dismiss, true);
    };
  }, [position]);
  const referenceLabel = zh ? '引用给 Agent' : 'Reference for Agent';
  const menuLabel = zh ? '资源菜单' : 'Resource menu';
  return <div className="library-resource-actions">
    <button type="button" className="library-resource-action" disabled={busy} aria-label={referenceLabel} data-tooltip={referenceLabel} onClick={async () => {
      setBusy(true);
      try {
        const { payload, text } = libraryReference(resource, path);
        showReferenceResult(await insertOrCopyReference(payload, text));
      } finally { setBusy(false); }
    }}>
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M10 6H3v7h4c0 2-1 3-3 4v2c4-1 6-4 6-8V6ZM21 6h-7v7h4c0 2-1 3-3 4v2c4-1 6-4 6-8V6Z" /></svg>
      {busy ? '…' : zh ? '引用' : 'Reference'}
    </button>
    <button ref={trigger} type="button" className="library-resource-action library-resource-kebab" aria-label={menuLabel} data-tooltip={menuLabel} aria-haspopup="menu" aria-expanded={!!position} aria-controls={position ? id : undefined} onClick={() => {
      if (position) { close(); return; }
      const rect = trigger.current!.getBoundingClientRect();
      setPosition({ left: Math.max(8, Math.min(rect.right - 180, innerWidth - 188)), top: Math.max(8, Math.min(rect.bottom + 4, innerHeight - (actions.length * 40 + 16))) });
    }}>⋮</button>
    {position && createPortal(<div ref={menu} id={id} role="menu" aria-label={menuLabel} className="library-folder-popup library-resource-menu" style={position}>
      {actions.map(action => <button key={action.id} type="button" role="menuitem" className={action.danger ? 'library-menu-danger' : undefined} onClick={() => { close(); action.run(); }}>{action.label}</button>)}
    </div>, document.body)}
  </div>;
}
