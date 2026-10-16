import { useCallback, useEffect, useRef, useState } from 'react';
import { t } from '../i18n';
import { api, json, hashFile, sendMissing, type Device, type Folder, type Resource, type Session } from './api';
import './library.css';

type Task = { id: string; name: string; size: number; bytes: number; phase: string; session?: Session; error?: string };
const size = (bytes: number) => `${(bytes / 1024 ** 2).toFixed(1)} MiB`;
const labels: Record<string, Parameters<typeof t>[0]> = { queued: 'libQueued', hashing: 'libHashing', uploading: 'libUploading', completing: 'libVerifying', completed: 'libDistributing', ready: 'libReady', partially_ready: 'libPartial', distributing: 'libDistributing', staged: 'libStaged', failed: 'libFailed', cancelled: 'libCancelled', cancelling: 'libCancelled', pending: 'libDistributing', downloading: 'libDistributing', waiting: 'libDistributing', registered: 'libUploading' };
const status = (value: string) => labels[value] ? t(labels[value]) : value;
export default function Library() {
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
  const loading = useRef(false);
  const purposes = useRef(new Map<string, string>());
  const refresh = useCallback(async () => {
    if (loading.current) return;
    loading.current = true;
    try {
      const [r, f, n] = await Promise.all([api<{ resources: Resource[] }>('/library/resources'), api<{ folders: Folder[] }>('/library/folders'), api<Device[]>('/nodes')]);
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
      if (!mounted.current) return;
      setResources(r.resources); setFolders(f.folders); setNodes(n); setSessions(all.filter(s => purposes.current.get(s.upload_id) !== "relay")); setError(''); setLoaded(true);
    } catch (e) { if (mounted.current) setError(`${t('libConnectionError')} ${String(e)}`); }
    finally { loading.current = false; }
  }, []);
  useEffect(() => {
    mounted.current = true; void refresh();
    const timer = setInterval(() => { if (!document.hidden) void refresh(); }, 5000);
    return () => { mounted.current = false; clearInterval(timer); controllers.current.forEach(c => c.abort()); };
  }, [refresh]);
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
        await refresh();
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
  return <main id="main" className="library-page">
    <div className="eyebrow">HOLOLAB / {t('library')}</div><h1>{t('library')}</h1><p className="page-intro">{t('libIntro')}</p>
    <div className="library-toolbar"><label>{t('libFolder')} <select value={folder} onChange={e => setFolder(e.target.value)}>{folderOptions}</select></label><button className="button" onClick={() => void refresh()}>{t('libRefresh')}</button><form onSubmit={e => { e.preventDefault(); void createFolder(); }}><input aria-label={t('libNewFolder')} value={folderName} onChange={e => setFolderName(e.target.value)} placeholder={t('libNewFolder')} /><button className="button" disabled={!folderName.trim() || creating}>{t('libCreate')}</button></form></div>
    {error && <p className="library-error" role="alert">{error}</p>}
    <section className={`library-drop${dragging ? ' is-dragging' : ''}`} aria-label={t('libDrop')} onDragOver={e => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; setDragging(true); }} onDragLeave={e => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragging(false); }} onDrop={e => { e.preventDefault(); setDragging(false); receive(Array.from(e.dataTransfer.files)); }}>
      <span className="library-upload-icon" aria-hidden="true">↑</span><h2>{t('libDrop')}</h2><p>{t('libDropHint')}</p><button className="button primary" disabled={!loaded} onClick={() => picker.current?.click()}>{t('libBrowse')}</button><input ref={picker} type="file" multiple hidden onChange={e => { receive(Array.from(e.target.files ?? [])); e.target.value = ''; }} />
    </section>
    {(tasks.length > 0 || pending.length > 0) && <section className="library-uploads" aria-label={t('libUploads')}><h2>{t('libUploads')}</h2><p>{t('libResumeHint')}</p>
      {tasks.map(task => { const resource = resources.find(r => r.resource_id === task.session?.resource_id); const phase = task.phase === 'completed' ? resource?.status ?? 'distributing' : task.phase; return <div className="library-upload" key={task.id}><div><strong>{task.name}</strong><span role="status">{status(phase)}{resource?.status === 'partially_ready' ? ` ${resource.ready_count}/${resource.target_count}` : ''}</span></div><progress aria-label={task.name} max={Math.max(1, task.size)} value={task.bytes} /><small>{size(task.bytes)} / {size(task.size)}</small>{task.error && <p role="alert">{task.error}</p>}{task.phase === 'failed' && <><button className="button" onClick={() => schedule(task, storedFiles.current.get(task.id)!, options.current.get(task.id)!)}>{t('libRetry')}</button>{task.session && <><label className="button">{t('libReselect')}<input type="file" hidden onChange={e => { if (e.target.files?.[0]) resume(task.session!, e.target.files[0]); e.target.value = ''; }} /></label><button className="button" onClick={() => void cancel(task.session!)}>{t('libCancelUpload')}</button></>}</>}</div>; })}
      {pending.map(s => <div className="library-upload" key={s.upload_id}><strong>{resources.find(r => r.resource_id === s.resource_id)?.name ?? s.upload_id}</strong><p>{status(s.state)} · {size(s.expected_size)}</p>{s.last_error && <p>{s.last_error}</p>}<label className="button">{t('libReselect')}<input type="file" hidden onChange={e => { if (e.target.files?.[0]) resume(s, e.target.files[0]); e.target.value = ''; }} /></label> <button className="button" onClick={() => void cancel(s)}>{t('libCancelUpload')}</button></div>)}
    </section>}
    <section className="library-resources"><h2>{t('libResources')}</h2><div className="library-table-wrap"><table><thead><tr>{['libName','libType','libSize','libPlacement','libStatus'].map(key => <th key={key}>{t(key as Parameters<typeof t>[0])}</th>)}</tr></thead><tbody>{resources.filter(r => r.folder_id === folder && r.status !== 'cancelled').map(r => <tr key={r.resource_id}><td>{r.name}</td><td>{r.name.split('.').length > 1 ? r.name.split('.').at(-1)?.toUpperCase() : r.resource_type}</td><td>{size(r.size_bytes)}</td><td>{r.placements.filter(p => p.desired_state === 'present').map(p => <span className={`library-chip ${p.observed_state === 'ready' ? 'ready' : ''}`} key={p.node_id} title={p.last_error ?? status(p.observed_state)}>{p.node_name} · {status(p.observed_state)}{!p.online && ` · ${t('libOffline')}`}</span>)}</td><td>{status(r.status)}{r.status === 'partially_ready' && ` ${r.ready_count}/${r.target_count}`}{r.transfers?.filter(item => item.last_error).map((item, i) => <small key={i}>{item.last_error}</small>)}</td></tr>)}</tbody></table></div>{loaded && !resources.some(r => r.folder_id === folder && r.status !== 'cancelled') && <p>{t('libEmpty')}</p>}</section>
    <dialog className="library-dialog" aria-labelledby="library-destination-title" ref={dialog} onCancel={() => setFiles([])} onClose={() => setFiles([])}><h2 id="library-destination-title">{t('libDestination')}</h2><p>{files.map(f => f.name).join(', ')}</p><label>{t('libFolder')}<select value={destination} onChange={e => setDestination(e.target.value)}>{folderOptions}</select></label><fieldset><legend>{t('libTargets')}</legend>{knownNodes.map(n => <label key={n.node_id}><input type="checkbox" checked={targets.includes(n.node_id)} disabled={n.online === false} onChange={e => setTargets(old => e.target.checked ? [...old, n.node_id] : old.filter(id => id !== n.node_id))} />{n.node_name}<small>{n.online === false ? t('libOffline') : t('libOnline')}</small></label>)}{!nodes.length && <p>{t('libNoNodes')}</p>}</fieldset><footer><button className="button" onClick={() => setFiles([])}>{t('libCancel')}</button><button className="button primary" disabled={!loaded || !targets.some(id => nodes.some(n => n.node_id === id && n.online !== false))} onClick={start}>{t('libStart')}</button></footer></dialog>
  </main>;
}
