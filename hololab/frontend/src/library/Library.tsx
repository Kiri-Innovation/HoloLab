import { newId } from '../newId';
import GalleryFolderMenu from './GalleryFolderMenu';
import { useCallback, useEffect, useRef, useState } from 'react';
import { getComputeNodes } from '../api';
import { ThemeToggle } from '../theme/ThemeToggle';
import { t } from './labels';
import { api, json, hashFile, sendMissing, type Device, type Folder, type Resource, type Session } from './api';
import './library.css';

type Task = { id: string; name: string; size: number; bytes: number; phase: string; session?: Session; error?: string; resource?: Resource };
const w = { remove: 'Delete resource', title: 'Delete this resource?', warning: 'This removes all Library copies. Offline devices will clean up when they reconnect. The name remains reserved until every device confirms deletion.', retry: 'Retry deletion', generic: 'Generic file', previous: 'Previous', next: 'Next', page: 'Page', folderDrop: 'Folder imports are not supported yet. Select files instead.' };
const deletingStatus = (n: number) => (document.documentElement.lang.toLowerCase().startsWith('zh') || navigator.language.toLowerCase().startsWith('zh')) ? `删除中 · 还有 ${n} 台设备持有` : `Deleting · ${n} ${n === 1 ? 'device' : 'devices'} remaining`;
function EntryIcon({ kind }: { kind: 'folder' | 'file' | 'upload' }) {
  return <svg viewBox="0 0 48 48" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {kind === 'folder' ? <path d="M5 14a3 3 0 0 1 3-3h12l5 5h15a3 3 0 0 1 3 3v19a3 3 0 0 1-3 3H8a3 3 0 0 1-3-3Z" /> : kind === 'upload' ? <><path d="M24 32V6m-10 10L24 6l10 10M7 31v8a3 3 0 0 0 3 3h28a3 3 0 0 0 3-3v-8" /></> : <><path d="M12 5h16l10 10v28H12ZM28 5v11h10M19 26h12M19 33h12" /></>}
  </svg>;
}
const size = (bytes: number) => `${(bytes / 1024 ** 2).toFixed(1)} MiB`;
const labels: Record<string, Parameters<typeof t>[0]> = { queued: 'libQueued', hashing: 'libHashing', uploading: 'libUploading', completing: 'libVerifying', completed: 'libDistributing', ready: 'libReady', partially_ready: 'libPartial', distributing: 'libDistributing', staged: 'libStaged', failed: 'libFailed', cancelled: 'libCancelled', cancelling: 'libCancelled', pending: 'libDistributing', downloading: 'libDistributing', waiting: 'libDistributing', registered: 'libUploading' };
const status = (value: string) => labels[value] ? t(labels[value]) : value;
export default function Library({ onBack }: { onBack: () => void }) {
  const [page, setPage] = useState(0);
  const [total, setTotal] = useState(0);
  const [deleting, setDeleting] = useState<Resource | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState('');
  const deleteDialog = useRef<HTMLDialogElement>(null);
  const [resources, setResources] = useState<Resource[]>([]);
  const [operations, setOperations] = useState<{operation_id:string; kind:string; old_path:string; folder_ids?:string[]; folder_remaining_nodes?:Record<string,number>; nodes:{node_id:string;node_name?:string;state:string;last_error:string|null}[]}[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [folders, setFolders] = useState<Folder[]>([]);
  const [nodes, setNodes] = useState<Device[]>([]);
  const [folder, setFolder] = useState(() => new URLSearchParams(window.location.search).get('folder') || 'root');
  const [destination, setDestination] = useState('root');
  const [targets, setTargets] = useState<string[]>([]);
  const [files, setFiles] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const dragDialog = useRef<HTMLDialogElement>(null);
  const dragDepth = useRef(0);
  const [tasks, setTasks] = useState<Task[]>([]);
  const tasksRef = useRef<Task[]>([]); tasksRef.current = tasks;
  const [error, setError] = useState('');
  const [loaded, setLoaded] = useState(false);
  const loadedRef = useRef(loaded); loadedRef.current = loaded;
  const [folderName, setFolderName] = useState('');
  const [creating, setCreating] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const storedFiles = useRef(new Map<string, File>());
  const options = useRef(new Map<string, { folder: string; targets: string[] }>());
  const controllers = useRef(new Map<string, AbortController>());
  const queue = useRef(Promise.resolve());
  const mounted = useRef(true);
  const refreshVersion = useRef(0);
  const purposes = useRef(new Map<string, string>());
  const refresh = useCallback(async () => {
    const version = ++refreshVersion.current;
    try {
      const [r, n] = await Promise.all([api<{ resources: Resource[]; total?: number }>(`/library/resources?folder_id=${encodeURIComponent(folder)}&limit=50&offset=${page * 50}`), getComputeNodes()]);
      const management = await api<{operations: typeof operations}>('/library/operations');
      const folderRows: Folder[] = [];
      for (let offset = 0; ; offset += 1000) {
        const f = await api<{ folders: Folder[] }>(`/library/folders?limit=1000&offset=${offset}`);
        folderRows.push(...f.folders); if (f.folders.length < 1000) break;
      }
      const all: Session[] = [];
      for (let offset = 0; ; offset += 1000) {
        const page = await api<{ sessions: Session[] }>(`/library/uploads?limit=1000&offset=${offset}`);
        all.push(...page.sessions);
        if (page.sessions.length < 1000) break;
      }
      // Relay sessions belong to node-to-node transfers, not browser uploads.
      for (const session of all.filter(s => ['uploading', 'completing'].includes(s.state))) {
        if (!purposes.current.has(session.upload_id)) {
          const detail = await api<Session>(`/library/uploads/${session.upload_id}`);
          purposes.current.set(session.upload_id, detail.purpose ?? 'upload');
        }
      }
      // Upload progress also follows resources outside the current folder/page.
      const completed = await Promise.all(tasksRef.current.filter(task => task.phase === 'completed' && task.session).map(async task => {
        const visible = r.resources.find(item => item.resource_id === task.session!.resource_id);
        const resource = visible ?? (task.resource?.status === 'ready' ? task.resource : await api<Resource>(`/library/resources/${task.session!.resource_id}`).catch(() => task.resource));
        return { id: task.id, resource };
      }));
      if (!mounted.current || version !== refreshVersion.current) return;
      setTasks(old => old.map(task => { const update = completed.find(item => item.id === task.id); return update ? { ...task, resource: update.resource } : task; }));
      setOperations(management.operations);
      setResources(r.resources); setTotal(r.total ?? r.resources.length); if (r.total !== undefined && page > 0 && page * 50 >= r.total) setPage(Math.max(0, Math.ceil(r.total / 50) - 1)); setFolders(folderRows);
      // /nodes is the same live inventory used by Compute Nodes. Retain nodes
      // seen on this page when they disconnect so selected targets turn disabled.
      setNodes(previous => [...n, ...previous.filter(old => !n.some(node => node.node_id === old.node_id)).map(old => ({ ...old, online: false }))]);
      setSessions(all.filter(s => purposes.current.get(s.upload_id) !== "relay")); setError(''); setLoaded(true);
    } catch (e) { if (mounted.current && version === refreshVersion.current) setError(`${t('libConnectionError')} ${String(e)}`); }

  }, [folder, page]);
  const latestRefresh = useRef(refresh);
  latestRefresh.current = refresh;
  useEffect(() => {
    void refresh();
    const timer = setInterval(() => { if (!document.hidden) void refresh(); }, 5000);
    return () => clearInterval(timer);
  }, [refresh]);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; controllers.current.forEach(c => c.abort()); }; }, []);
  useEffect(() => { if (deleting) deleteDialog.current?.showModal(); else deleteDialog.current?.close(); }, [deleting]);
  async function confirmDelete() {
    if (!deleting || deleteBusy) return;
    setDeleteBusy(true); setDeleteError('');
    try { await api(`/library/resources/${deleting.resource_id}`, { method: 'DELETE' }); setTasks(old => old.filter(task => task.session?.resource_id !== deleting.resource_id)); setDeleting(null); await refresh(); }
    catch (e) { setDeleteError(String(e)); }
    finally { setDeleteBusy(false); }
  }
  useEffect(() => { if (files.length) dialog.current?.showModal(); else dialog.current?.close(); }, [files]);
  const patch = (id: string, change: Partial<Task>) => { if (mounted.current) setTasks(old => old.map(item => item.id === id ? { ...item, ...change } : item)); };
  function schedule(task: Task, file: File, config: { folder: string; targets: string[] }) {
    storedFiles.current.set(task.id, file); options.current.set(task.id, config);
    patch(task.id, { phase: 'queued', error: undefined });
    queue.current = queue.current.then(async () => {
      if (!mounted.current) return;
      const controller = new AbortController(); controllers.current.set(task.id, controller);
      let session = task.session;
      try {
        patch(task.id, { phase: 'hashing', bytes: 0 });
        const digest = await hashFile(file, bytes => patch(task.id, { bytes }), controller.signal);
        if (session && (digest !== session.expected_sha256 || file.size !== session.expected_size)) throw new Error(t('libWrongFile'));
        if (!session) {
          session = await api<Session>('/library/uploads', { ...json({ filename: file.name, size_bytes: file.size, sha256: digest, folder_id: config.folder, target_node_ids: config.targets }), signal: controller.signal });
          patch(task.id, { session });
        }
        session = await api<Session>(`/library/uploads/${session.upload_id}`, { signal: controller.signal });
        if (session.state !== 'completed') {
          patch(task.id, { phase: 'uploading', session, bytes: 0 });
          await sendMissing(file, session, bytes => patch(task.id, { bytes }), controller.signal);
          patch(task.id, { phase: 'completing' });
          session = await api<Session>(`/library/uploads/${session.upload_id}/complete`, { method: 'POST', signal: controller.signal });
        }
        patch(task.id, { phase: 'completed', session, bytes: file.size });
        storedFiles.current.delete(task.id); options.current.delete(task.id);
        await latestRefresh.current();
      } catch (e) { patch(task.id, { phase: 'failed', session, error: String(e) }); }
      finally { controllers.current.delete(task.id); }
    });
  }
  function receive(incoming: File[]) { if (!incoming.length) return; setDestination(folder); setTargets([]); setFiles(incoming); }
  // Capture-phase document listeners also cover the top bar, dialogs and empty space.
  useEffect(() => {
    const isFileDrag = (event: DragEvent) => Array.from(event.dataTransfer?.types ?? []).includes('Files');
    const reset = () => { dragDepth.current = 0; setDragging(false); dragDialog.current?.close(); };
    const enter = (event: DragEvent) => {
      if (!isFileDrag(event)) return;
      event.preventDefault(); dragDepth.current += 1; setDragging(true);
    };
    const over = (event: DragEvent) => {
      if (!isFileDrag(event)) return;
      event.preventDefault(); if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy'; setDragging(true);
    };
    const leave = (event: DragEvent) => {
      if (!dragDepth.current && !isFileDrag(event)) return;
      dragDepth.current = Math.max(0, dragDepth.current - 1);
      if (!dragDepth.current || (!event.relatedTarget && (event.clientX <= 0 || event.clientY <= 0 || event.clientX >= window.innerWidth || event.clientY >= window.innerHeight))) reset();
    };
    const drop = (event: DragEvent) => {
      if (!isFileDrag(event)) return;
      event.preventDefault(); reset();
      if (!loadedRef.current) { setError('Wait for the current folder to load before uploading.'); return; }
      if (Array.from(event.dataTransfer?.items ?? []).some(item => item.webkitGetAsEntry?.()?.isDirectory)) { setError(w.folderDrop); return; }
      receive(Array.from(event.dataTransfer?.files ?? []));
    };
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape' && dragDialog.current?.open) { event.preventDefault(); reset(); } };
    document.addEventListener('dragenter', enter, true);
    document.addEventListener('dragover', over, true);
    document.addEventListener('dragleave', leave, true);
    document.addEventListener('drop', drop, true);
    document.addEventListener('dragend', reset, true);
    document.addEventListener('keydown', escape, true);
    window.addEventListener('blur', reset);
    return () => {
      document.removeEventListener('dragenter', enter, true); document.removeEventListener('dragover', over, true);
      document.removeEventListener('dragleave', leave, true); document.removeEventListener('drop', drop, true);
      document.removeEventListener('dragend', reset, true); document.removeEventListener('keydown', escape, true);
      window.removeEventListener('blur', reset); reset();
    };
  }, [folder]);
  useEffect(() => { if (dragging) dragDialog.current?.showModal(); else dragDialog.current?.close(); }, [dragging]);
  function openFolder(id: string) {
    if (id === folder) { void refresh(); return; }
    refreshVersion.current += 1; setLoaded(false); setResources([]); setFolder(id); setPage(0);
    const url = new URL(window.location.href);
    if (id === 'root') url.searchParams.delete('folder'); else url.searchParams.set('folder', id);
    window.history.replaceState(window.history.state, '', url);
  }
  function start() {
    const validTargets = targets.filter(id => nodes.some(n => n.node_id === id && n.online !== false));
    if (!validTargets.length) return;
    const newTasks = files.map(file => ({ id: newId(), name: file.name, size: file.size, bytes: 0, phase: 'queued' }));
    setTasks(old => [...newTasks, ...old]);
    files.forEach((file, i) => schedule(newTasks[i], file, { folder: destination, targets: validTargets }));
    setFiles([]);
  }
  async function cancel(session: Session) {
    try { await api(`/library/uploads/${session.upload_id}`, { method: 'DELETE' }); setTasks(old => old.filter(task => task.session?.upload_id !== session.upload_id)); await refresh(); }
    catch (e) { setError(String(e)); }
  }
  function resume(session: Session, file: File) {
    const existing = tasks.find(task => task.session?.upload_id === session.upload_id);
    const task: Task = { id: existing?.id ?? newId(), name: file.name, size: file.size, bytes: 0, phase: 'queued', session };
    setTasks(old => [task, ...old.filter(item => item.id !== task.id)]);
    schedule(task, file, { folder: 'root', targets: [] });
  }
  async function createFolder() {
    if (!folderName.trim() || creating) return;
    setCreating(true);
    try { await api('/library/folders', json({ name: folderName.trim(), parent_id: folder })); setFolderName(''); await refresh(); }
    catch (e) { setError(String(e)); } finally { setCreating(false); }
  }
  function folderPath(id: string): string {
    const names: string[] = []; const seen = new Set<string>();
    while (id !== 'root' && !seen.has(id)) { seen.add(id); const f = folders.find(f => f.folder_id === id); if (!f) break; names.unshift(f.name); id = f.parent_id ?? 'root'; }
    return '/' + names.join('/');
  }
  const breadcrumbs: Folder[] = [];
  const visited = new Set<string>();
  let ancestor = folder;
  while (ancestor !== 'root' && !visited.has(ancestor)) {
    visited.add(ancestor); const item = folders.find(f => f.folder_id === ancestor); if (!item) break;
    breadcrumbs.unshift(item); ancestor = item.parent_id ?? 'root';
  }
  const children = folders.filter(f => f.parent_id === folder).sort((a, b) => a.name.localeCompare(b.name));
  const visibleResources = resources.filter(r => r.folder_id === folder && r.status !== 'cancelled');
  const pending = sessions.filter(s => ['uploading', 'completing'].includes(s.state) && !tasks.some(task => task.session?.upload_id === s.upload_id));
  const knownNodes = [...nodes, ...resources.flatMap(r => r.placements).filter((p, i, all) => !nodes.some(n => n.node_id === p.node_id) && all.findIndex(x => x.node_id === p.node_id) === i).map(p => ({ node_id: p.node_id, node_name: p.node_name, online: false }))];
  return <div className="hl-app-shell library-shell"><header className="hl-topbar library-topbar"><strong>HoloLab</strong><span>· Library</span><button onClick={onBack}>← Workflows</button><ThemeToggle /></header><main id="main" className="library-page">
    <div className="library-heading"><div><span className="library-eyebrow">YOUR ASSETS</span><h1>{t('library')}</h1></div><button className="button" disabled={!loaded} onClick={() => picker.current?.click()}>↑ {t('libBrowse')}</button><input className="library-file-picker" ref={picker} type="file" multiple hidden onChange={e => { receive(Array.from(e.target.files ?? [])); e.target.value = ''; }} /></div>
    <div className="library-toolbar"><nav className="library-breadcrumbs" aria-label="Folder path"><button aria-current={folder === 'root' ? 'page' : undefined} onClick={() => openFolder('root')}>Library</button>{breadcrumbs.map(item => <span key={item.folder_id}><span aria-hidden="true">/</span><button aria-current={folder === item.folder_id ? 'page' : undefined} onClick={() => openFolder(item.folder_id)}>{item.name}</button></span>)}</nav><button className="button" onClick={() => void refresh()}>{t('libRefresh')}</button><form onSubmit={e => { e.preventDefault(); void createFolder(); }}><input aria-label={t('libNewFolder')} value={folderName} onChange={e => setFolderName(e.target.value)} placeholder={t('libNewFolder')} /><button className="button" disabled={!folderName.trim() || creating || !loaded}>{t('libCreate')}</button></form></div>
    {error && <p className="library-error" role="alert">{error}</p>}
    {operations.map(op => <div className="library-operation" role="status" key={op.operation_id}>
      {op.kind.startsWith('delete_') ? 'Deleting folder' : 'Renaming'} · {op.old_path} · {op.nodes.filter(n => n.state !== 'completed').length} devices remaining
      <p>Library writes are paused until every device confirms this directory operation.</p>
      {op.nodes.filter(n => n.last_error).map(n => <p role="alert" key={n.node_id}>{n.node_name ?? n.node_id}: {n.last_error}</p>)}
      {op.nodes.some(n => n.state === 'failed') && <button onClick={async () => { try { await api(`/library/operations/${op.operation_id}/retry`, {method:'POST'}); await refresh(); } catch(e) { setError(String(e)); } }}>Retry failed devices</button>}
    </div>)}
    <GalleryFolderMenu folderId={folder} disabled={!loaded || operations.length > 0} onCreated={() => latestRefresh.current()} folders={folders} resources={resources} devices={knownNodes} onOpen={openFolder} onDelete={r => { setDeleteError(''); setDeleting(r); }} path={folderPath}>
      <div className="library-folder-summary"><span>{children.length} {children.length === 1 ? 'folder' : 'folders'} · {total} {total === 1 ? 'file' : 'files'}</span><span>Drop files anywhere to upload</span></div>
      <div className="library-gallery">
        {children.map(child => {
          const deletion = operations.find(op => op.folder_ids?.includes(child.folder_id));
          const remaining = deletion?.folder_remaining_nodes?.[child.folder_id] ?? deletion?.nodes.filter(n => n.state !== 'completed').length ?? 0;
          return <button className="library-entry library-folder-entry" data-folder-id={child.folder_id} key={child.folder_id} onClick={() => openFolder(child.folder_id)} aria-label={`Open folder ${child.name}`}><div className="library-entry-art"><EntryIcon kind="folder" /></div><div className="library-entry-body"><strong>{child.name}</strong><span className="library-entry-meta">{deletion ? deletingStatus(remaining) : <>Folder <span aria-hidden="true">↗</span></>}</span></div></button>; })}
        {visibleResources.map(r => <article className="library-entry library-file-entry" data-resource-id={r.resource_id} key={r.resource_id} aria-label={r.name}><div className="library-entry-art"><EntryIcon kind="file" /><span>{r.name.includes('.') ? r.name.split('.').slice(-1)[0].toUpperCase() : 'FILE'}</span></div><div className="library-entry-body"><strong>{r.name}</strong><span className="library-entry-meta">{r.resource_type === 'generic' ? w.generic : r.resource_type} · {size(r.size_bytes)}</span><div className="library-entry-status" role="status">{r.status === 'deleting' ? deletingStatus(r.remaining_nodes ?? 0) : status(r.status)}{r.status === 'partially_ready' && ` ${r.ready_count}/${r.target_count}`}</div><div className="library-placements">{r.placements.filter(p => p.desired_state === 'present' || (r.status === 'deleting' && p.observed_state !== 'absent')).map(p => <span className={`library-chip ${p.observed_state === 'ready' ? 'ready' : ''}`} key={p.node_id} title={p.last_error ?? status(p.observed_state)}>{p.node_name} · {p.observed_state === 'deleting' ? 'Deleting' : status(p.observed_state)}{!p.online && ` · ${t('libOffline')}`}</span>)}</div>{r.transfers?.filter(item => item.last_error).map((item, i) => <small className="library-error" key={i}>{item.last_error}</small>)}{r.gc_errors?.map((item, i) => <small className="library-error" key={`gc-${i}`}>{item.last_error}</small>)}<button className="button library-delete" onClick={() => { setDeleteError(''); setDeleting(r); }}>{r.status === 'deleting' ? w.retry : w.remove}</button></div></article>)}
      </div>
      {loaded && !children.length && !visibleResources.length && <div className="library-empty"><EntryIcon kind="folder" /><h2>This folder is empty</h2><p>Create a folder or drop files anywhere to add them here.</p></div>}
      {!loaded && <p role="status">Loading folder…</p>}
      {total > 50 && <nav className="library-pagination" aria-label={w.page}><button className="button" disabled={page === 0} onClick={() => setPage(p => p - 1)}>{w.previous}</button><span>{w.page} {page + 1} / {Math.max(1, Math.ceil(total / 50))}</span><button className="button" disabled={(page + 1) * 50 >= total} onClick={() => setPage(p => p + 1)}>{w.next}</button></nav>}
    </GalleryFolderMenu>
    <dialog ref={dragDialog} className="library-drag-overlay" aria-labelledby="library-drop-title" onCancel={() => { dragDepth.current = 0; setDragging(false); }}><div className="library-drag-frame"><EntryIcon kind="upload" /><h2 id="library-drop-title">Release to upload to</h2><p className="library-drop-path">{folderPath(folder)}</p><p>Choose your devices next · {t('libDropHint')}</p></div></dialog>
    {(tasks.length > 0 || pending.length > 0) && <section className="library-uploads" aria-label={t('libUploads')}><h2>{t('libUploads')}</h2><p>{t('libResumeHint')}</p>
      {tasks.map(task => { const resource = resources.find(r => r.resource_id === task.session?.resource_id) ?? task.resource; const phase = task.phase === 'completed' ? resource?.status ?? 'distributing' : task.phase; return <div className="library-upload" key={task.id}><div><strong>{task.name}</strong><span role="status">{status(phase)}{resource?.status === 'partially_ready' ? ` ${resource.ready_count}/${resource.target_count}` : ''}</span></div><progress aria-label={task.name} max={Math.max(1, task.size)} value={task.bytes} /><small>{size(task.bytes)} / {size(task.size)}</small>{task.error && <p role="alert">{task.error}</p>}{task.phase === 'failed' && <><button className="button" onClick={() => schedule(task, storedFiles.current.get(task.id)!, options.current.get(task.id)!)}>{t('libRetry')}</button>{task.session && <><label className="button">{t('libReselect')}<input type="file" hidden onChange={e => { if (e.target.files?.[0]) resume(task.session!, e.target.files[0]); e.target.value = ''; }} /></label><button className="button" onClick={() => void cancel(task.session!)}>{t('libCancelUpload')}</button></>}</>}</div>; })}
      {pending.map(s => <div className="library-upload" key={s.upload_id}><strong>{resources.find(r => r.resource_id === s.resource_id)?.name ?? s.upload_id}</strong><p>{status(s.state)} · {size(s.expected_size)}</p>{s.last_error && <p>{s.last_error}</p>}<label className="button">{t('libReselect')}<input type="file" hidden onChange={e => { if (e.target.files?.[0]) resume(s, e.target.files[0]); e.target.value = ''; }} /></label> <button className="button" onClick={() => void cancel(s)}>{t('libCancelUpload')}</button></div>)}
    </section>}
    <dialog className="library-dialog" ref={deleteDialog} aria-labelledby="library-delete-title" onCancel={e => { if (deleteBusy) e.preventDefault(); else setDeleting(null); }}><h2 id="library-delete-title">{w.title}</h2>{deleting && <><p><strong>{deleting.name}</strong></p><p>{folderPath(deleting.folder_id).replace(/\/$/, '')}/{deleting.name}</p><p>{size(deleting.size_bytes)}</p><p>{deleting.placements.filter(p => p.observed_state !== 'absent').map(p => `${p.node_name}${!p.online ? ` · ${t('libOffline')}` : ''}`).join(', ') || 'Gateway staging only'}</p><p>{w.warning}</p></>}{deleteError && <p role="alert" className="library-error">{deleteError}</p>}<footer><button className="button" disabled={deleteBusy} onClick={() => setDeleting(null)}>{t('libCancel')}</button><button className="button library-delete-confirm" disabled={deleteBusy} onClick={() => void confirmDelete()}>{w.remove}</button></footer></dialog>
    <dialog className="library-dialog" aria-labelledby="library-destination-title" ref={dialog} onCancel={() => setFiles([])} onClose={() => setFiles([])}><h2 id="library-destination-title">{t('libDestination')}</h2><p>{files.map(f => f.name).join(', ')}</p><p className="library-destination-path">Uploading to <strong>{folderPath(destination)}</strong></p><fieldset><legend>{t('libTargets')}</legend>{knownNodes.map(n => <label key={n.node_id}><input type="checkbox" checked={targets.includes(n.node_id)} disabled={n.online === false} onChange={e => setTargets(old => e.target.checked ? [...old, n.node_id] : old.filter(id => id !== n.node_id))} />{n.node_name}<small>{n.online === false ? t('libOffline') : t('libOnline')}</small></label>)}{!nodes.length && <p>{t('libNoNodes')}</p>}</fieldset><footer><button className="button" onClick={() => setFiles([])}>{t('libCancel')}</button><button className="button hl-button--primary" disabled={!loaded || !targets.some(id => nodes.some(n => n.node_id === id && n.online !== false))} onClick={start}>{t('libStart')}</button></footer></dialog>
  </main></div>;
}
