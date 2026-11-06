import { useEffect, useRef, useState } from 'react';
import { useViewerInteraction, ViewerInteractionShield } from './ViewerInteraction';
import { versionedViewerUrl } from './viewerBuild';
import type { loadColmap4dPayload } from './colmap4dPayload';
import { imageDirectorySize, imagePath, LOAD_CONFIRM_BYTES, pointSize } from './colmap4dResources';

type Payload = Awaited<ReturnType<typeof loadColmap4dPayload>>;
type Part = 'images' | 'points';
type Resource = {bytes?: number; state: 'checking' | 'ask' | 'skipped' | 'loading' | 'loaded' | 'error'; detail?: string};
const formatSize = (n?: number) => n == null ? '体积未知' : n >= 1e9 ? `${(n / 1e9).toFixed(2)} GB` : `${(n / 1e6).toFixed(1)} MB`;

export function Colmap4DPreview({baseUrl, imagesUrl, camsOnly = false, nodeId, absolutePath}: {
  baseUrl: string; imagesUrl: string; camsOnly?: boolean; nodeId?: string; absolutePath?: string;
}) {
  const {wrapperRef, activated, activate} = useViewerInteraction();
  const frame = useRef<HTMLIFrameElement>(null);
  const [ready, setReady] = useState(false);
  const [revision, setRevision] = useState(0);
  const [status, setStatus] = useState('读取相机 / 位姿 / 时间轴…');
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  const [parts, setParts] = useState<Record<Part, Resource>>({images: {state: 'checking'}, points: {state: 'checking'}});
  const payload = useRef<Payload['files']>([]);
  const sent = useRef(false);
  const cameraView = useRef<Payload['cameraView']>();
  const actions = useRef<(part: Part) => void>(() => {});
  useEffect(() => {
    const receive = (event: MessageEvent) => {
      if (event.source !== frame.current?.contentWindow || event.origin !== location.origin) return;
      if (event.data?.type === 'hololab-viewer-ready' && cameraView.current) {
        frame.current?.contentWindow?.postMessage({type: 'hololab-viewer-camera', managed: true, has_default: true, view: cameraView.current}, location.origin);
      }
      if (event.data?.type !== 'colmap-ready') return;
      setReady(true);
      if (sent.current || !payload.current.length) return;
      sent.current = true;
      frame.current?.contentWindow?.postMessage({type: 'colmap-load-files', files: payload.current, name: 'colmap4d preview'}, location.origin);
    };
    window.addEventListener('message', receive);
    return () => window.removeEventListener('message', receive);
  }, []);
  useEffect(() => {
    const abort = new AbortController(); const workers = new Set<Worker>(); let alive = true;
    const files = new Map<string, Blob>(); const busy = new Set<Part>(); let names: string[] = [];
    setError(false); setStatus('读取相机 / 位姿 / 时间轴…');
    setParts({images: {state: 'checking'}, points: {state: 'checking'}});
    const update = (part: Part, patch: Partial<Resource>) => { if (alive) setParts(old => ({...old, [part]: {...old[part], ...patch}})); };
    // Rebuild only the bounded in-memory fileset. A new frame prevents overlapping
    // asynchronous processFiles calls; no cameras or previously loaded bytes refetch.
    const publish = (added: Payload['files']) => {
      if (!alive) return;
      for (const file of added) files.set(file.name, file.blob);
      payload.current = [...files].map(([name, blob]) => ({name, blob}));
      sent.current = false; setReady(false); setRevision(n => n + 1);
    };
    const load = (phase: 'cameras' | 'points') => new Promise<Payload>((resolve, reject) => {
      const worker = new Worker(new URL('./colmap4d.worker.ts', import.meta.url), {type: 'module'}); workers.add(worker);
      const done = () => { worker.terminate(); workers.delete(worker); };
      worker.onmessage = event => {
        if (event.data.type === 'loaded') { done(); resolve(event.data.payload); }
        else if (event.data.type === 'error') { done(); reject(new Error(event.data.text)); }
        else if (alive) {
          if (phase === 'cameras') setStatus(event.data.text);
          else update('points', {detail: event.data.text});
        }
      };
      worker.onerror = event => { done(); reject(new Error(event.message)); };
      worker.postMessage({baseUrl, phase});
    });
    const start = async (part: Part) => {
      if (busy.has(part) || !alive) return;
      busy.add(part); update(part, {state: 'loading', detail: undefined});
      try {
        if (part === 'points') {
          const p = await load('points');
          // Remove the camera-only placeholder before installing TXT or BIN points.
          files.delete('points3D.bin'); publish(p.files);
          update(part, {state: 'loaded', detail: `${p.shown < p.total ? '预览抽样' : '全部点'}：${p.shown.toLocaleString()} / ${p.total.toLocaleString()} 点`});
        } else {
          const added: Payload['files'] = [];
          // Bounded network concurrency; no image download until this stage is allowed.
          let cursor = 0;
          await Promise.all(Array.from({length: Math.min(4, names.length)}, async () => {
            while (cursor < names.length) {
              const name = names[cursor++];
              const r = await fetch(`${imagesUrl}/${imagePath(name)}`, {signal: abort.signal});
              if (!r.ok) throw new Error(`${name}: HTTP ${r.status}`);
              added.push({name: `images/${name}`, blob: await r.blob()});
            }
          }));
          publish(added); update(part, {state: 'loaded', detail: `${added.length} 张图片`});
        }
      } catch (e) { update(part, {state: 'error', detail: (e as Error).message}); }
      finally { busy.delete(part); }
    };
    actions.current = part => { void start(part); };
    const decide = async (part: Part) => {
      try {
        let bytes: number;
        if (part === 'points') {
          const size = await pointSize(baseUrl, abort.signal); bytes = size.bytes;
          update(part, {detail: `几何 ${formatSize(size.geometry)} + 时间 ${formatSize(size.times)}`});
        } else {
          if (!nodeId || !absolutePath) throw new Error('无法取得目录体积，请手动选择');
          bytes = await imageDirectorySize(nodeId, `${absolutePath}/images`, abort.signal);
        }
        update(part, {bytes, state: 'ask'});
        if (bytes <= LOAD_CONFIRM_BYTES && alive) void start(part);
      } catch (e) { update(part, {state: 'ask', detail: (e as Error).message}); }
    };
    void load('cameras').then(p => {
      if (!alive) return;
      cameraView.current = p.cameraView;
      names = [...new Set(p.imageNames)]; publish(p.files);
      setStatus(`相机 / 位姿 / 时间轴已加载${p.estimated ? ' · 合成/估计时间' : ''}`);
      if (!camsOnly) { void decide('images'); void decide('points'); }
    }).catch(e => { if (alive) { setError(true); setStatus(e.message); } });
    return () => { alive = false; abort.abort(); workers.forEach(w => w.terminate()); };
  }, [baseUrl, imagesUrl, camsOnly, nodeId, absolutePath, retry]);
  return <div className="nodrag nowheel" style={{width: '100%'}}>
    <div role="status" style={{background: '#171b24', fontSize: 11, padding: '6px 8px', color: error ? 'var(--error)' : '#e5e7eb'}}>
      {status} {error && <button onClick={() => setRetry(n => n + 1)}>重试</button>}
    </div>
    {!camsOnly && <div style={{background: '#222936', color: '#f3f4f6', padding: 8, fontSize: 12}}>
      <div>超过 {LOAD_CONFIRM_BYTES / 1e6} MB 或体积未知时由你选择；点云仍最多预览 20 万点。</div>
      {(['images', 'points'] as Part[]).map(part => <div key={part} data-resource={part} style={{paddingTop: 6}}>
        {part === 'images' ? '图片' : '点云'} {formatSize(parts[part].bytes)} — {' '}
        {({checking: '测量中', ask: '等待选择', skipped: '已跳过', loading: '加载中', loaded: '已加载', error: '加载失败'})[parts[part].state]}{' '}
        {['ask', 'skipped', 'error'].includes(parts[part].state) && <button onClick={() => actions.current(part)}>加载</button>}{' '}
        {parts[part].state === 'ask' && <button onClick={() => setParts(old => ({...old, [part]: {...old[part], state: 'skipped'}}))}>跳过</button>}
        {parts[part].detail && <div style={{opacity: .8}}>{parts[part].detail}</div>}
      </div>)}
    </div>}
    <div ref={wrapperRef} data-viewer-interaction={activated ? 'active' : 'inactive'} style={{position: 'relative'}}>
      <iframe key={revision} ref={frame} title="colmap4d viewer" src={versionedViewerUrl('/colmaputil/index.html?embed=1')}
        sandbox="allow-scripts allow-same-origin" style={{width: '100%', height: 420, border: 0, display: 'block', background: '#000'}} />
      {ready && !error && !activated && <ViewerInteractionShield activate={activate} />}
    </div>
  </div>;
}
