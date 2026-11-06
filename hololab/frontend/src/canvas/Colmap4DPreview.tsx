import { useEffect, useRef, useState } from 'react';
import { useViewerInteraction, ViewerInteractionShield } from './ViewerInteraction';
import { versionedViewerUrl } from './viewerBuild';
import type { loadColmap4dPayload } from './colmap4dPayload';

type Payload = Awaited<ReturnType<typeof loadColmap4dPayload>>;

/** Separate lifecycle/budget from the existing unmodified COLMAP TXT bridge. */
export function Colmap4DPreview({baseUrl}: {baseUrl: string}) {
  const {wrapperRef, activated, activate} = useViewerInteraction();
  const frame = useRef<HTMLIFrameElement>(null);
  const [ready, setReady] = useState(false);
  const [status, setStatus] = useState('等待 ColmapUtil…');
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const receive = (event: MessageEvent) => {
      if (event.source === frame.current?.contentWindow && event.origin === location.origin && event.data?.type === 'colmap-ready') setReady(true);
    };
    window.addEventListener('message', receive);
    return () => window.removeEventListener('message', receive);
  }, []);
  useEffect(() => {
    if (!ready) return;
    setError(false); setStatus('读取 colmap4d…');
    const worker = new Worker(new URL('./colmap4d.worker.ts', import.meta.url), {type: 'module'});
    worker.onmessage = (event: MessageEvent) => {
      if (event.data.type === 'loaded') {
        const p = event.data.payload as Payload;
        setStatus(`${p.shown < p.total ? '预览抽样' : '全部点'}：${p.shown.toLocaleString()} / ${p.total.toLocaleString()} 点${p.estimated ? ' · 合成/估计时间' : ''}`);
        frame.current?.contentWindow?.postMessage({type: 'colmap-load-files', files: p.files, name: 'colmap4d preview'}, location.origin);
        worker.terminate();
      } else {
        setStatus(event.data.text); setError(event.data.type === 'error');
        if (event.data.type === 'error') worker.terminate();
      }
    };
    worker.onerror = event => { setError(true); setStatus(event.message); worker.terminate(); };
    worker.postMessage({baseUrl});
    return () => worker.terminate();
  }, [baseUrl, ready, retry]);
  return <div ref={wrapperRef} data-viewer-interaction={activated ? "active" : "inactive"} style={{position: 'relative', width: '100%'}}>
    <div role="status" style={{background: '#171b24', fontSize: 11, padding: '6px 8px', color: error ? 'var(--error)' : '#e5e7eb', overflowWrap: 'anywhere'}}>
      {status} {error && <button onClick={() => setRetry(n => n + 1)}>重试</button>}
    </div>
    <iframe ref={frame} title="colmap4d viewer" src={versionedViewerUrl('/colmaputil/index.html?embed=1')}
      sandbox="allow-scripts allow-same-origin" style={{width: '100%', height: 420, border: 0, display: 'block', background: '#000'}} />
    {ready && !error && !activated && <ViewerInteractionShield activate={activate} />}
  </div>;
}
