import { useCallback, useEffect, useRef, useState } from 'react';
import { getComputeNodes } from '../api';
import { ThemeToggle } from '../theme/ThemeToggle';
import { t } from './labels';
import { api, json, hashFile, sendMissing, type Device, type Folder, type Resource, type Session } from './api';
import './library.css';

type Task = { id: string; name: string; size: number; bytes: number; phase: string; session?: Session; error?: string; resource?: Resource };
const w = { remove: 'Delete resource', title: 'Delete this resource?', warning: 'This removes all Library copies. Offline devices will clean up when they reconnect. The name remains reserved until every device confirms deletion.', retry: 'Retry deletion', generic: 'Generic file', previous: 'Previous', next: 'Next', page: 'Page', folderDrop: 'Folder imports are not supported yet. Select files instead.' };
const deletingStatus = (n: number) => `Deleting · ${n} ${n === 1 ? 'device' : 'devices'} remaining`;
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
  const [sessions, setSessions] = useState<Session[]>([]);
  const [folders, setFolders] = useState<Folder[]>([]);
  const [nodes, setNodes] = useState<Device[]>([]);
  const [folder, setFolder] = useState('root');
  const [destination, setDestination] = useState('root');
  const [targets, setTargets] = useState<string[]>([]);
  const [files, setFiles] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const [tasks, setTasks] = useState<Task[]>([]);
  const tasksRef = useRef<Task[]>([]); tasksRef.current = tasks;
  const [error, setError] = useState('');
  const [loaded, setLoaded] = useState(false);
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
  function start() {
    const validTargets = targets.filter(id => nodes.some(n => n.node_id === id && n.online !== false));
    if (!validTargets.length) return;
    const newTasks = files.map(file => ({ id: crypto.randomUUID(), name: file.name, size: file.size, bytes: 0, phase: 'queued' }));
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
    const task: Task = { id: existing?.id ?? crypto.randomUUID(), name: file.name, size: file.size, bytes: 0, phase: 'queued', session };
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
  const folderOptions = <><option value="root">{t('libRoot')}</option>{folders.filter(f => f.folder_id !== 'root').map(f => <option key={f.folder_id} value={f.folder_id}>{folderPath(f.folder_id)}</option>)}</>;
  const pending = sessions.filter(s => ['uploading', 'completing'].includes(s.state) && !tasks.some(task => task.session?.upload_id === s.upload_id));
  const knownNodes = [...nodes, ...resources.flatMap(r => r.placements).filter((p, i, all) => !nodes.some(n => n.node_id === p.node_id) && all.findIndex(x => x.node_id === p.node_id) === i).map(p => ({ node_id: p.node_id, node_name: p.node_name, online: false }))];
  return <div className="hl-app-shell library-shell"><header className="hl-topbar library-topbar"><strong>HoloLab</strong><span>· Library</span><button onClick={onBack}>← Workflows</button><ThemeToggle /></header><main id="main" className="library-page">
    <h1>{t('library')}</h1><p className="page-intro">{t('libIntro')}</p>
    <div className="library-toolbar"><label>{t('libFolder')} <select value={folder} onChange={e => { setFolder(e.target.value); setPage(0); }}>{folderOptions}</select></label><button className="button" onClick={() => void refresh()}>{t('libRefresh')}</button><form onSubmit={e => { e.preventDefault(); void createFolder(); }}><input aria-label={t('libNewFolder')} value={folderName} onChange={e => setFolderName(e.target.value)} placeholder={t('libNewFolder')} /><button className="button" disabled={!folderName.trim() || creating}>{t('libCreate')}</button></form></div>
    {error && <p className="library-error" role="alert">{error}</p>}
    <section className={`library-drop${dragging ? ' is-dragging' : ''}`} aria-label={t('libDrop')} onDragOver={e => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; setDragging(true); }} onDragLeave={e => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragging(false); }} onDrop={e => { e.preventDefault(); setDragging(false); if (Array.from(e.dataTransfer.items).some(item => item.webkitGetAsEntry?.()?.isDirectory)) { setError(w.folderDrop); return; } receive(Array.from(e.dataTransfer.files)); }}>
      <span className="library-upload-icon" aria-hidden="true">↑</span><h2>{t('libDrop')}</h2><p>{t('libDropHint')}</p><button className="button hl-button--primary" disabled={!loaded} onClick={() => picker.current?.click()}>{t('libBrowse')}</button><input ref={picker} type="file" multiple hidden onChange={e => { receive(Array.from(e.target.files ?? [])); e.target.value = ''; }} />
    </section>
    {(tasks.length > 0 || pending.length > 0) && <section className="library-uploads" aria-label={t('libUploads')}><h2>{t('libUploads')}</h2><p>{t('libResumeHint')}</p>
      {tasks.map(task => { const resource = resources.find(r => r.resource_id === task.session?.resource_id) ?? task.resource; const phase = task.phase === 'completed' ? resource?.status ?? 'distributing' : task.phase; return <div className="library-upload" key={task.id}><div><strong>{task.name}</strong><span role="status">{status(phase)}{resource?.status === 'partially_ready' ? ` ${resource.ready_count}/${resource.target_count}` : ''}</span></div><progress aria-label={task.name} max={Math.max(1, task.size)} value={task.bytes} /><small>{size(task.bytes)} / {size(task.size)}</small>{task.error && <p role="alert">{task.error}</p>}{task.phase === 'failed' && <><button className="button" onClick={() => schedule(task, storedFiles.current.get(task.id)!, options.current.get(task.id)!)}>{t('libRetry')}</button>{task.session && <><label className="button">{t('libReselect')}<input type="file" hidden onChange={e => { if (e.target.files?.[0]) resume(task.session!, e.target.files[0]); e.target.value = ''; }} /></label><button className="button" onClick={() => void cancel(task.session!)}>{t('libCancelUpload')}</button></>}</>}</div>; })}
      {pending.map(s => <div className="library-upload" key={s.upload_id}><strong>{resources.find(r => r.resource_id === s.resource_id)?.name ?? s.upload_id}</strong><p>{status(s.state)} · {size(s.expected_size)}</p>{s.last_error && <p>{s.last_error}</p>}<label className="button">{t('libReselect')}<input type="file" hidden onChange={e => { if (e.target.files?.[0]) resume(s, e.target.files[0]); e.target.value = ''; }} /></label> <button className="button" onClick={() => void cancel(s)}>{t('libCancelUpload')}</button></div>)}
    </section>}
    <section className="library-resources"><h2>{t('libResources')}</h2><div className="library-table-wrap"><table><thead><tr>{['libName','libType','libSize','libPlacement','libStatus'].map(key => <th key={key}>{t(key as Parameters<typeof t>[0])}</th>)}<th>{w.remove}</th></tr></thead><tbody>{resources.filter(r => r.folder_id === folder && r.status !== 'cancelled').map(r => <tr key={r.resource_id}><td>{r.name}</td><td>{r.resource_type === 'generic' ? w.generic : r.resource_type}</td><td>{size(r.size_bytes)}</td><td>{r.placements.filter(p => p.desired_state === 'present' || (r.status === 'deleting' && p.observed_state !== 'absent')).map(p => <span className={`library-chip ${p.observed_state === 'ready' ? 'ready' : ''}`} key={p.node_id} title={p.last_error ?? status(p.observed_state)}>{p.node_name} · {p.observed_state === 'deleting' ? 'Deleting' : status(p.observed_state)}{!p.online && ` · ${t('libOffline')}`}</span>)}</td><td>{r.status === 'deleting' ? deletingStatus(r.remaining_nodes ?? 0) : status(r.status)}{r.status === 'partially_ready' && ` ${r.ready_count}/${r.target_count}`}{r.transfers?.filter(item => item.last_error).map((item, i) => <small key={i}>{item.last_error}</small>)}{r.gc_errors?.map((item, i) => <small key={`gc-${i}`}>{item.last_error}</small>)}</td><td><button className="button library-delete" onClick={() => { setDeleteError(''); setDeleting(r); }}>{r.status === 'deleting' ? w.retry : w.remove}</button></td></tr>)}</tbody></table></div>{loaded && !resources.some(r => r.folder_id === folder && r.status !== 'cancelled') && <p>{t('libEmpty')}</p>}<nav className="library-pagination" aria-label={w.page}><button className="button" disabled={page === 0} onClick={() => setPage(p => p - 1)}>{w.previous}</button><span>{w.page} {page + 1} / {Math.max(1, Math.ceil(total / 50))}</span><button className="button" disabled={(page + 1) * 50 >= total} onClick={() => setPage(p => p + 1)}>{w.next}</button></nav></section>
    <dialog className="library-dialog" ref={deleteDialog} aria-labelledby="library-delete-title" onCancel={e => { if (deleteBusy) e.preventDefault(); else setDeleting(null); }}><h2 id="library-delete-title">{w.title}</h2>{deleting && <><p><strong>{deleting.name}</strong></p><p>{folderPath(deleting.folder_id).replace(/\/$/, '')}/{deleting.name}</p><p>{size(deleting.size_bytes)}</p><p>{deleting.placements.filter(p => p.observed_state !== 'absent').map(p => `${p.node_name}${!p.online ? ` · ${t('libOffline')}` : ''}`).join(', ') || 'Gateway staging only'}</p><p>{w.warning}</p></>}{deleteError && <p role="alert" className="library-error">{deleteError}</p>}<footer><button className="button" disabled={deleteBusy} onClick={() => setDeleting(null)}>{t('libCancel')}</button><button className="button library-delete-confirm" disabled={deleteBusy} onClick={() => void confirmDelete()}>{w.remove}</button></footer></dialog>
    <dialog className="library-dialog" aria-labelledby="library-destination-title" ref={dialog} onCancel={() => setFiles([])} onClose={() => setFiles([])}><h2 id="library-destination-title">{t('libDestination')}</h2><p>{files.map(f => f.name).join(', ')}</p><label>{t('libFolder')}<select value={destination} onChange={e => setDestination(e.target.value)}>{folderOptions}</select></label><fieldset><legend>{t('libTargets')}</legend>{knownNodes.map(n => <label key={n.node_id}><input type="checkbox" checked={targets.includes(n.node_id)} disabled={n.online === false} onChange={e => setTargets(old => e.target.checked ? [...old, n.node_id] : old.filter(id => id !== n.node_id))} />{n.node_name}<small>{n.online === false ? t('libOffline') : t('libOnline')}</small></label>)}{!nodes.length && <p>{t('libNoNodes')}</p>}</fieldset><footer><button className="button" onClick={() => setFiles([])}>{t('libCancel')}</button><button className="button hl-button--primary" disabled={!loaded || !targets.some(id => nodes.some(n => n.node_id === id && n.online !== false))} onClick={start}>{t('libStart')}</button></footer></dialog>
  </main></div>;
}
