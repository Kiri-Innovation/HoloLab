// Decimal MB, intentionally independent of the 200,000-point rendering budget.
export const LOAD_CONFIRM_BYTES = 200 * 1000 * 1000;
export const imagePath = (name: string) => {
  if (!name || name.startsWith('/') || name.includes('\\') || name.split('/').some(p => p === '..' || p === '.')) throw new Error('Unsafe image path');
  return name.split('/').map(encodeURIComponent).join('/');
};

export async function fileSize(url: string, signal?: AbortSignal): Promise<number> {
  const timeout = AbortSignal.timeout(15000);
  const r = await fetch(url, {cache: 'no-store', headers: {Range: 'bytes=0-0'}, signal: signal ? AbortSignal.any([signal, timeout]) : timeout});
  const size = r.status === 206 ? r.headers.get('content-range')?.split('/')[1] : r.headers.get('content-length');
  if (r.status === 206) await r.arrayBuffer();
  else await r.body?.cancel();
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  if (!size || !Number.isSafeInteger(Number(size))) throw new Error('Unknown file size');
  return Number(size);
}

export async function pointSize(base: string, signal?: AbortSignal) {
  async function table(stem: string) {
    try { return await fileSize(`${base}/${stem}.bin`, signal); }
    catch (e) { if ((e as Error).message !== 'HTTP 404') throw e; return fileSize(`${base}/${stem}.txt`, signal); }
  }
  const [geometry, times] = await Promise.all([table('points3D'), table('points_t')]);
  return {bytes: geometry + times, geometry, times};
}

/** Existing node metadata RPC: paginated stat only, no image bodies or backend upgrade. */
export async function imageDirectorySize(nodeId: string, path: string, signal: AbortSignal): Promise<number> {
  const pending = [path]; let bytes = 0, visited = 0;
  while (pending.length) {
    if (++visited > 10000) throw new Error('Too many image directories');
    const directory = pending.pop()!; let offset: number | null = 0;
    do {
      const query = new URLSearchParams({path: directory, offset: String(offset), limit: '200'});
      const r = await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/paths?${query}`, {signal});
      if (!r.ok) throw new Error(`Image metadata: HTTP ${r.status}`);
      const data = await r.json();
      for (const entry of data.entries) {
        if (entry.symlink || entry.kind === 'unavailable') throw new Error('Image directory size unavailable');
        if (entry.kind === 'directory') pending.push(entry.path);
        else if (entry.kind === 'file' && Number.isSafeInteger(entry.size)) bytes += entry.size;
        else throw new Error('Image directory size unavailable');
      }
      offset = data.next_offset;
    } while (offset != null);
  }
  return bytes;
}
