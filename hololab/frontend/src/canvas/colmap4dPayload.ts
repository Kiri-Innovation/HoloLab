/** Bounded preview bridge. Stream source tables; never materialize full point/track maps.
 * This is a display-only subset, not a reconstruction export. IDs and ns stay intact.
 */
export const POINT_BUDGET = 200_000;
const SMALL_LIMIT = 16 * 1024 * 1024;
const view = (b: Uint8Array) => new DataView(b.buffer, b.byteOffset, b.byteLength);
const count = (b: Uint8Array, offset = 0) => {
  const n = Number(view(b).getBigUint64(offset, true));
  if (!Number.isSafeInteger(n)) throw new Error('Unsafe table count');
  return n;
};
const prefix = (n: number) => {
  const b = new Uint8Array(8); view(b).setBigUint64(0, BigInt(n), true); return b;
};

class Stream {
  private reader: ReadableStreamDefaultReader<Uint8Array>;
  private chunk: Uint8Array = new Uint8Array(0);
  private pos = 0;
  constructor(response: Response, private progress: (n: number) => void) {
    if (!response.body) throw new Error('Streaming response required');
    this.reader = response.body.getReader();
  }
  async read(n: number, keep = true): Promise<Uint8Array> {
    if (!Number.isSafeInteger(n) || n < 0) throw new Error('Invalid record length');
    if (n <= this.chunk.length - this.pos) {
      const b = keep ? this.chunk.subarray(this.pos, this.pos + n) : new Uint8Array(0);
      this.pos += n; return b;
    }
    // Only callers requesting small headers allocate. Skipped observations/tracks never do.
    if (keep && n > SMALL_LIMIT) throw new Error('Preview record exceeds budget');
    const out = new Uint8Array(keep ? n : 0);
    let done = 0;
    while (done < n) {
      if (this.pos === this.chunk.length) {
        const next = await this.reader.read();
        if (next.done) throw new Error('Truncated COLMAP table');
        this.chunk = next.value; this.pos = 0; this.progress(next.value.length);
      }
      const take = Math.min(n - done, this.chunk.length - this.pos);
      if (keep) out.set(this.chunk.subarray(this.pos, this.pos + take), done);
      this.pos += take; done += take;
    }
    return out;
  }
  async close() { await this.reader.cancel(); }
}

export async function loadColmap4dPayload(base: string, progress: (text: string) => void, budget = POINT_BUDGET) {
  let bytes = 0, last = 0;
  const report = (n: number) => {
    bytes += n;
    if (Date.now() - last > 250) { progress(`读取预览：${(bytes / 1048576).toFixed(0)} MiB（流式扫描）`); last = Date.now(); }
  };
  const root = base.replace(/\/$/, '');
  async function open(name: string) {
    const r = await fetch(`${root}/${name}`);
    if (!r.ok) { await r.body?.cancel(); throw new Error(`${name}: HTTP ${r.status}`); }
    return r;
  }
  async function table(name: string) {
    const r = await fetch(`${root}/${name}.bin`);
    if (r.ok) return { binary: true, response: r };
    await r.body?.cancel();
    if (r.status !== 404) throw new Error(`${name}.bin: HTTP ${r.status}`);
    return { binary: false, response: await open(`${name}.txt`) };
  }
  async function small(r: Response) {
    const reader = r.body!.getReader(), parts: Uint8Array<ArrayBuffer>[] = []; let size = 0;
    try {
      for (;;) {
        const v = await reader.read(); if (v.done) break;
        size += v.value.length; report(v.value.length);
        if (size > SMALL_LIMIT) throw new Error('Camera/metadata table exceeds preview budget');
        parts.push(v.value.slice());
      }
    } finally { await reader.cancel(); }
    return new Blob(parts);
  }
  async function* lines(r: Response) {
    const reader = r.body!.getReader(), decoder = new TextDecoder(); let pending = '';
    try {
      for (;;) {
        const part = await reader.read();
        pending += part.done ? decoder.decode() : decoder.decode(part.value, { stream: true });
        if (!part.done) report(part.value.length);
        let start = 0, end: number;
        while ((end = pending.indexOf('\n', start)) >= 0) { yield pending.slice(start, end).replace(/\r$/, ''); start = end + 1; }
        pending = pending.slice(start);
        if (pending.length > 64 * 1024 * 1024) throw new Error('TXT line exceeds preview budget');
        if (part.done) { if (pending) yield pending; break; }
      }
    } finally { await reader.cancel(); }
  }
  const files: {name: string; blob: Blob}[] = [];
  const cams = await table('cameras');
  files.push({ name: `cameras.${cams.binary ? 'bin' : 'txt'}`, blob: await small(cams.response) });
  const images = await table('images');
  if (images.binary) {
    const s = new Stream(images.response, report);
    try {
      const n = count(await s.read(8));
      if (n > 100_000) throw new Error('Too many cameras for preview (100,000 maximum)');
      const parts: Uint8Array<ArrayBuffer>[] = [prefix(n)]; let retainedBytes = 8;
      for (let i = 0; i < n; i++) {
        parts.push((await s.read(64)).slice());
        const name: number[] = [];
        do { name.push((await s.read(1))[0]); if (name.length > 65536) throw new Error('Invalid image name'); } while (name[name.length - 1]);
        retainedBytes += 72 + name.length;
        if (retainedBytes > SMALL_LIMIT) throw new Error('Image headers exceed preview budget');
        parts.push(new Uint8Array(name));
        const observations = count(await s.read(8));
        parts.push(prefix(0)); await s.read(observations * 24, false);
      }
      files.push({name: 'images.bin', blob: new Blob(parts)});
    } finally { await s.close(); }
  } else {
    const rows: string[] = []; let pose = true, retainedChars = 0;
    for await (const line of lines(images.response)) {
      if (line.trimStart().startsWith('#')) continue;
      if (pose && !line.trim()) continue;
      if (pose) {
        retainedChars += line.length;
        if (retainedChars > SMALL_LIMIT) throw new Error('Image headers exceed preview budget');
        rows.push(line + '\n\n');
      }
      pose = !pose;
      if (rows.length > 100_000) throw new Error('Too many cameras for preview');
    }
    if (!pose) throw new Error('Missing image observation row');
    files.push({name: 'images.txt', blob: new Blob(rows)});
  }
  const points = await table('points3D');
  const selected = new Set<bigint>(); let total = 0;
  if (points.binary) {
    const s = new Stream(points.response, report);
    try {
      total = count(await s.read(8)); const stride = Math.max(1, Math.ceil(total / budget));
      const parts: Uint8Array<ArrayBuffer>[] = [];
      for (let i = 0; i < total; i++) {
        const h = await s.read(51), tracks = count(h, 43);
        if (i % stride === 0) {
          const row = h.slice(); row.fill(0, 43);
          selected.add(view(row).getBigUint64(0, true)); parts.push(row);
        }
        await s.read(tracks * 8, false);
      }
      files.push({name: 'points3D.bin', blob: new Blob([prefix(parts.length), ...parts])});
    } finally { await s.close(); }
  } else {
    // Deterministic reservoir: uniform over row order, bounded even without a count header.
    const rows: string[] = []; let seed = 0x12345678;
    for await (const line of lines(points.response)) {
      if (!line.trim() || line.trimStart().startsWith('#')) continue;
      total++;
      seed ^= seed << 13; seed ^= seed >>> 17; seed ^= seed << 5;
      const slot = total <= budget ? total - 1 : Math.floor((seed >>> 0) / 4294967296 * total);
      if (slot < budget) {
        const fields = line.trim().split(/\s+/, 8);
        if (fields.length < 8) throw new Error('Invalid point row');
        rows[slot] = fields.join(' ') + '\n';
      }
    }
    for (const row of rows) selected.add(BigInt(row.split(' ', 1)[0]));
    files.push({name: 'points3D.txt', blob: new Blob(rows)});
  }
  for (const name of ['times', 'points_t']) {
    const t = await table(name), pointTime = name === 'points_t';
    const records = new Map<bigint, bigint>();
    if (t.binary) {
      const s = new Stream(t.response, report);
      try {
        const n = count(await s.read(8)), width = pointTime ? 16 : 12;
        if (!pointTime && n > 100_000) throw new Error('Too many image times');
        // Batch fixed-size sidecars: no per-source-point async overhead or BigInt map.
        for (let i = 0; i < n;) {
          const take = Math.min(32768, n - i), b = await s.read(take * width), d = view(b);
          for (let j = 0; j < take; j++) {
            const offset = j * width, id = pointTime ? d.getBigUint64(offset, true) : BigInt(d.getUint32(offset, true));
            if (!pointTime || selected.has(id)) records.set(id, d.getBigInt64(offset + width - 8, true));
          }
          i += take;
        }
      } finally { await s.close(); }
    } else {
      for await (const line of lines(t.response)) {
        if (!line.trim() || line.trimStart().startsWith('#')) continue;
        const fields = line.trim().split(/\s+/);
        if (fields.length !== 2) throw new Error(`Invalid ${name} row`);
        const id = BigInt(fields[0]), ns = BigInt(fields[1]);
        if (!pointTime || selected.has(id)) records.set(id, ns);
        if (!pointTime && records.size > 100_000) throw new Error('Too many image times');
      }
    }
    const width = pointTime ? 16 : 12, b = new Uint8Array(8 + records.size * width), d = view(b);
    d.setBigUint64(0, BigInt(records.size), true); let offset = 8;
    for (const [id, ns] of records) {
      if (pointTime) d.setBigUint64(offset, id, true); else d.setUint32(offset, Number(id), true);
      d.setBigInt64(offset + width - 8, ns, true); offset += width;
    }
    files.push({name: `${name}.bin`, blob: new Blob([b])});
  }
  const metaBlob = await small(await open('time_meta.json'));
  const meta = JSON.parse(await metaBlob.text());
  if (!meta || typeof meta !== 'object' || Array.isArray(meta)) throw new Error('Invalid time_meta.json');
  files.push({name: 'time_meta.json', blob: metaBlob});
  const estimated = meta.conversion?.estimated === true || String(meta.conversion?.timestamp_mode ?? '').includes('synthetic');
  return {files, total, shown: selected.size, bytes, estimated};
}
