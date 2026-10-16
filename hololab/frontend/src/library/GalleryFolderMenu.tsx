import { useEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { api, json } from './api';

// Follow ProcessMenu's portal, focus and dismissal pattern without its process actions.
export default function GalleryFolderMenu({ folderId, disabled, onCreated, children }: {
  folderId: string; disabled: boolean; onCreated: () => Promise<void>; children: ReactNode;
}) {
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null);
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const submitting = useRef(false);
  const popup = useRef<HTMLDivElement>(null);
  const gallery = useRef<HTMLElement>(null);
  const close = () => setPosition(null);
  useEffect(close, [folderId]);
  useEffect(() => {
    if (!position) return;
    popup.current?.querySelector<HTMLElement>(editing ? 'input' : 'button')?.focus();
    const outside = (event: PointerEvent) => { if (!popup.current?.contains(event.target as Node)) close(); };
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); close(); gallery.current?.focus(); }
    };
    window.addEventListener('pointerdown', outside);
    window.addEventListener('keydown', key);
    window.addEventListener('resize', close);
    window.addEventListener('scroll', close, true);
    return () => {
      window.removeEventListener('pointerdown', outside); window.removeEventListener('keydown', key);
      window.removeEventListener('resize', close); window.removeEventListener('scroll', close, true);
    };
  }, [position, editing]);
  async function create() {
    if (!name.trim() || submitting.current) return;
    submitting.current = true; setBusy(true); setError('');
    try {
      await api('/library/folders', json({ name: name.trim(), parent_id: folderId }));
      close(); await onCreated();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { submitting.current = false; setBusy(false); }
  }
  return <section ref={gallery} className="library-resources" aria-label="Current folder" aria-busy={disabled} tabIndex={-1} onContextMenu={event => {
    if (disabled || submitting.current || (event.target as HTMLElement).closest('.library-entry, button, input')) return;
    event.preventDefault(); setEditing(false); setName(''); setError('');
    setPosition({ left: Math.max(8, Math.min(event.clientX, innerWidth - 288)), top: Math.max(8, Math.min(event.clientY, innerHeight - 156)) });
  }}>
    {children}
    {position && createPortal(<div ref={popup} className="library-folder-popup" style={position} onContextMenu={e => e.preventDefault()} onBlur={event => {
      if (!event.currentTarget.contains(event.relatedTarget as Node | null)) close();
    }}>
      {editing ? <form onSubmit={event => { event.preventDefault(); void create(); }}>
        <label htmlFor="library-inline-folder">New folder</label>
        <input id="library-inline-folder" autoComplete="off" value={name} readOnly={busy} onChange={event => setName(event.target.value)} aria-describedby="library-inline-hint" aria-invalid={!!error} />
        <small id="library-inline-hint">{busy ? 'Creating…' : 'Enter to create · Esc to cancel'}</small>
        {error && <p role="alert">{error}</p>}
      </form> : <div role="menu" aria-label="Gallery actions"><button role="menuitem" onClick={() => setEditing(true)}>New folder</button></div>}
    </div>, document.body)}
  </section>;
}
