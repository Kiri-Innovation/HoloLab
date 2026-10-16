import { locale } from '../i18n';
export const CHUNK = 8 * 1024 ** 2;
// Production uses the website's same-origin /api reverse proxy.
export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, { ...init, credentials: 'same-origin' });
  const body = await response.json().catch(() => null);
  if (response.status === 409 && typeof body?.detail === 'string' && body.detail.includes('name already exists')) throw new Error(locale === 'en' ? 'A file or folder with this name already exists. Choose another name; overwriting is not allowed.' : '该目录已存在同名文件或文件夹。请更换名称，不允许覆盖。');
  if (!response.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : `HTTP ${response.status}`);
  if (!body) throw new Error('Invalid API response');
  return body as T;
}
export const json = (body: unknown): RequestInit => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
export type Session = { upload_id: string; resource_id: string; version: number; state: string; expected_size: number; expected_sha256: string; chunk_size: number; parts?: { part_index: number; size_bytes: number }[]; last_error: string | null; purpose?: string };
export type Resource = { resource_id: string; name: string; folder_id: string; resource_type: string; size_bytes: number; status: string; remaining_nodes?: number; gc_errors?: { last_error: string }[]; ready_count: number; target_count: number; placements: { node_id: string; node_name: string; online: number; desired_state: string; observed_state: string; last_error: string | null }[]; transfers: { last_error: string | null }[] };
export type Folder = { folder_id: string; parent_id: string | null; name: string };
export type Device = { node_id: string; node_name: string; online?: boolean };
export function hashFile(file: File, progress: (bytes: number) => void, signal: AbortSignal): Promise<string> {
  return new Promise((resolve, reject) => {
    const worker = new Worker(new URL('./hash.worker.ts', import.meta.url), { type: 'module' });
    const stop = () => { worker.terminate(); signal.removeEventListener('abort', abort); };
    const abort = () => { stop(); reject(new DOMException('Aborted', 'AbortError')); };
    signal.addEventListener('abort', abort, { once: true });
    if (signal.aborted) { abort(); return; }
    worker.onmessage = ({ data }) => {
      if (data.error) { stop(); reject(new Error(data.error)); }
      else if (data.digest) { stop(); resolve(data.digest); }
      else progress(data.progress);
    };
    worker.onerror = event => { stop(); reject(new Error(event.message)); };
    worker.postMessage(file);
  });
}
export async function sendMissing(file: File, session: Session, progress: (bytes: number) => void, signal: AbortSignal) {
  if (session.chunk_size !== CHUNK) throw new Error('Unsupported upload chunk size');
  const received = new Set(session.parts?.map(p => p.part_index));
  let done = session.parts?.reduce((sum, part) => sum + part.size_bytes, 0) ?? 0;
  progress(done);
  const missing = Array.from({ length: Math.ceil(file.size / CHUNK) }, (_, i) => i).filter(i => !received.has(i));
  let next = 0;
  let failure: unknown;
  await Promise.all(Array.from({ length: 3 }, async () => {
    while (next < missing.length && !failure && !signal.aborted) {
      const index = missing[next++];
      try {
        const chunk = await file.slice(index * CHUNK, (index + 1) * CHUNK).arrayBuffer();
        const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', chunk)), b => b.toString(16).padStart(2, '0')).join('');
        await api(`/library/uploads/${session.upload_id}/parts/${index}`, { method: 'PUT', headers: { 'X-Chunk-Sha256': digest }, body: chunk, signal });
        done += chunk.byteLength; progress(done);
      } catch (error) { failure = error; }
    }
  }));
  if (failure) throw failure;
  signal.throwIfAborted();
}
