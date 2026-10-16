import { useEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { api, json, type Folder, type Resource, type Device } from './api';

// Follow ProcessMenu's portal, focus and dismissal pattern without its process actions.
export default function GalleryFolderMenu({ folderId, disabled, onCreated, children, folders, resources, devices, onOpen, onDelete, path }: {
  folderId: string; disabled: boolean; onCreated: () => Promise<void>; children: ReactNode;
  folders: Folder[]; resources: Resource[]; devices: Device[]; onOpen: (id: string) => void;
  onDelete: (resource: Resource) => void; path: (id: string) => string;
}) {
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null);
  const [target, setTarget] = useState<Folder | Resource | null>(null);
  const [deleting, setDeleting] = useState<Folder | null>(null);
  const confirmation = useRef<HTMLDialogElement>(null);
  useEffect(() => { if (deleting) confirmation.current?.showModal(); else confirmation.current?.close(); }, [deleting]);
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
      if (!editing && ['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
        event.preventDefault();
        const items = Array.from(popup.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') ?? []);
        const index = items.indexOf(document.activeElement as HTMLButtonElement);
        items[event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : (index + (event.key === 'ArrowUp' ? -1 : 1) + items.length) % items.length]?.focus();
      }
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
      await api(target && 'folder_id' in target ? `/library/folders/${target.folder_id}/rename` : '/library/folders', json({ name: name.trim(), parent_id: folderId }));
      close(); await onCreated();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { submitting.current = false; setBusy(false); }
  }
  return <section ref={gallery} className="library-resources" aria-label="Current folder" aria-busy={disabled} tabIndex={-1} onContextMenu={event => {
    if (disabled || submitting.current) return;
    const element = event.target as HTMLElement;
    const entry = element.closest<HTMLElement>('.library-entry');
    if (!entry && element.closest('button, input')) return;
    setTarget(entry?.dataset.folderId ? folders.find(f => f.folder_id === entry.dataset.folderId) ?? null : entry?.dataset.resourceId ? resources.find(r => r.resource_id === entry.dataset.resourceId) ?? null : null);
    event.preventDefault(); setEditing(false); setName(''); setError('');
    setPosition({ left: Math.max(8, Math.min(event.clientX, innerWidth - 288)), top: Math.max(8, Math.min(event.clientY, innerHeight - 156)) });
  }}>
    {children}
    {deleting && createPortal(<dialog ref={confirmation} className="library-dialog library-folder-delete" aria-label="Delete folder?" onCancel={() => setDeleting(null)}>
      <h2>Delete folder?</h2><p><strong>{deleting.name}</strong></p><p>{path(deleting.folder_id)}</p>
      <p>Only empty folders can be deleted. Every registered device confirms removal of its local directory; offline devices remain pending.</p>
      <p>{devices.map(n => `${n.node_name}${n.online === false ? ' · Offline' : ''}`).join(', ') || 'No registered devices'}</p>
      {error && <p role="alert">{error}</p>}
      <footer><button disabled={busy} onClick={() => setDeleting(null)}>Cancel</button><button className="library-delete-confirm" disabled={busy} onClick={async () => {
        if (submitting.current) return; submitting.current = true; setBusy(true); setError('');
        try { await api(`/library/folders/${deleting.folder_id}`, {method:'DELETE'}); setDeleting(null); await onCreated(); }
        catch (e) { setError(e instanceof Error ? e.message : String(e)); }
        finally { submitting.current = false; setBusy(false); }
      }}>Delete folder</button></footer>
    </dialog>, document.body)}
    {position && createPortal(<div ref={popup} className="library-folder-popup" style={position} onContextMenu={e => e.preventDefault()} onBlur={event => {
      if (!event.currentTarget.contains(event.relatedTarget as Node | null)) close();
    }}>
      {editing ? <form onSubmit={event => { event.preventDefault(); void create(); }}>
        <label htmlFor="library-inline-folder">{target ? "Rename folder" : "New folder"}</label>
        <input id="library-inline-folder" autoComplete="off" value={name} readOnly={busy} onChange={event => setName(event.target.value)} aria-describedby="library-inline-hint" aria-invalid={!!error} />
        <small id="library-inline-hint">{busy ? 'Creating…' : target ? 'Enter to rename · Esc to cancel' : 'Enter to create · Esc to cancel'}</small>
        {error && <p role="alert">{error}</p>}
      </form> : <div role="menu" aria-label="Gallery actions">
        {!target && <button role="menuitem" onClick={() => setEditing(true)}>New folder</button>}
        {target && !('resource_id' in target) && <>
          <button role="menuitem" onClick={() => { close(); onOpen(target.folder_id); }}>Open</button>
          <button role="menuitem" onClick={() => { setName(target.name); setEditing(true); }}>Rename</button>
          <button role="menuitem" className="library-menu-danger" onClick={() => { close(); setError(''); setDeleting(target); }}>Delete</button>
        </>}
        {target && 'resource_id' in target && <>
          <button role="menuitem" disabled={target.status === 'deleting'} onClick={() => { close(); window.open(`/api/library/resources/${target.resource_id}/download`, '_blank', 'noopener'); }}>Download</button>
          <button role="menuitem" onClick={async () => { try { await navigator.clipboard.writeText(path(target.folder_id).replace(/\/$/, '') + '/' + target.name); close(); } catch { setError('Could not copy the Library-relative path. Check clipboard permissions.'); } }}>Copy path</button>
          <button role="menuitem" className="library-menu-danger" onClick={() => { close(); onDelete(target); }}>Delete</button>
        </>}
        {error && <p role="alert">{error}</p>}
      </div>}
    </div>, document.body)}
  </section>;
}
