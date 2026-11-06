import { afterEach, expect, test, vi } from 'vitest';
import { loadColmap4dPayload } from './colmap4dPayload';

class Bytes extends Uint8Array<ArrayBuffer> {
  static get [Symbol.species]() { return Uint8Array; }
  constructor(value: number | ArrayBuffer | Uint8Array) { super(typeof value === "number" ? new ArrayBuffer(value) : value instanceof Uint8Array ? value.slice().buffer : value); }
  static alloc(n: number) { return new Bytes(n); }
  static wrap(value: string | ArrayBuffer) { return new Bytes(typeof value === 'string' ? new TextEncoder().encode(value) : value); }
  static concat(parts: Uint8Array[]) { const b = new Bytes(parts.reduce((n,p)=>n+p.length,0)); let offset=0; for(const p of parts){b.set(p,offset);offset+=p.length;} return b; }
  writeBigUInt64LE(n: bigint, offset=0) { new DataView(this.buffer).setBigUint64(offset,n,true); }
  writeBigInt64LE(n: bigint, offset=0) { new DataView(this.buffer).setBigInt64(offset,n,true); }
  writeUInt32LE(n: number, offset=0) { new DataView(this.buffer).setUint32(offset,n,true); }
  writeDoubleLE(n: number, offset=0) { new DataView(this.buffer).setFloat64(offset,n,true); }
  readBigUInt64LE(offset=0) { return new DataView(this.buffer).getBigUint64(offset,true); }
  readBigInt64LE(offset=0) { return new DataView(this.buffer).getBigInt64(offset,true); }
}
const u64 = (n: number) => { const b = Bytes.alloc(8); b.writeBigUInt64LE(BigInt(n)); return b; };
function binaryFiles() {
  const image = Bytes.alloc(64); image.writeUInt32LE(7);
  const points = Array.from({length: 7}, (_, i) => {
    const b = Bytes.alloc(51); b.writeBigUInt64LE(BigInt(100 + i)); b.writeDoubleLE(i, 8); b.writeBigUInt64LE(BigInt(i % 3), 43);
    return Bytes.concat([b, Bytes.alloc(i % 3 * 8)]);
  });
  const times = Bytes.alloc(12); times.writeUInt32LE(7); times.writeBigInt64LE(1700000000000000001n, 4);
  const pt = [106, 100, 103].map(id => { const b = Bytes.alloc(16); b.writeBigUInt64LE(BigInt(id)); b.writeBigInt64LE(BigInt(id) + 1700000000000000001n, 8); return b; });
  return new Map<string, Bytes | string>([
    ['cameras.bin', u64(0)],
    ['images.bin', Bytes.concat([u64(1), image, Bytes.wrap('a.png\0'), u64(2), Bytes.alloc(48)])],
    ['points3D.bin', Bytes.concat([u64(7), ...points])],
    ['times.bin', Bytes.concat([u64(1), times])],
    ['points_t.bin', Bytes.concat([u64(3), ...pt])],
    ['time_meta.json', '{"conversion":{"estimated":true}}'],
  ]);
}
function serve(files: Map<string, Bytes | string>) {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    const body = files.get(url.split('/').at(-1)!);
    if (body === undefined) return new Response('', {status:404});
    const bytes = typeof body === 'string' ? new TextEncoder().encode(body) : body;
    // Force records, strings and counters to cross network chunk boundaries.
    return new Response(new ReadableStream({start(c) {
      for (let i=0; i<bytes.length; i+=13) c.enqueue(bytes.slice(i,i+13)); c.close();
    }}));
  }));
}
afterEach(() => vi.unstubAllGlobals());
test('samples variable-track BIN, strips observations, joins unordered times by original ID without losing ns', async () => {
  serve(binaryFiles());
  const p = await loadColmap4dPayload('/model', () => {}, 3);
  expect([p.total,p.shown,p.estimated]).toEqual([7,3,true]);
  const file = async (name: string) => Bytes.wrap(await p.files.find(f=>f.name===name)!.blob.arrayBuffer());
  const points = await file('points3D.bin');
  expect(points.length).toBe(8+3*51);
  expect([0,1,2].map(i=>points.readBigUInt64LE(8+i*51))).toEqual([100n,103n,106n]);
  const images = await file('images.bin'); expect(images.length).toBe(8+64+6+8); expect(images.readBigUInt64LE(images.length-8)).toBe(0n);
  const pt = await file('points_t.bin'); expect(pt.readBigUInt64LE()).toBe(3n); expect(pt.readBigInt64LE(16)).toBe(1700000000000000107n);
});
test('small models keep every point including points with no time', async () => {
  serve(binaryFiles()); const p = await loadColmap4dPayload('/model',()=>{}); expect(p.shown).toBe(7);
});
test('TXT fallback handles blank observation rows, samples and forwards all sidecars', async () => {
  serve(new Map<string, Bytes | string>([
    ['cameras.txt','# cameras\n'],['images.txt','7 1 0 0 0 0 0 0 1 a.png\n\n'],
    ['points3D.txt',Array.from({length:12},(_,i)=>`${i+1} 1 2 3 4 5 6 0 7 8\n`).join('')],
    ['times.txt','7 1700000000000000001\n'],['points_t.txt','1 123\n'],['time_meta.json','{}'],
  ]));
  const p = await loadColmap4dPayload('/model',()=>{},3); expect([p.total,p.shown]).toEqual([12,3]);
  expect(p.files.map(f=>f.name)).toEqual(['cameras.txt','images.txt','points3D.txt','times.bin','points_t.bin','time_meta.json']);
});
test('rejects truncated time sidecars rather than showing a silent static model', async () => {
  const files=binaryFiles(); files.set('times.bin',u64(1)); serve(files);
  await expect(loadColmap4dPayload('/model',()=>{})).rejects.toThrow('Truncated');
});
test('camera stage seeks over observations and never requests points or point times', async () => {
  const files = binaryFiles();
  vi.stubGlobal('fetch', vi.fn(async (url: string, options?: RequestInit) => {
    const name = url.split('/').at(-1)!;
    if (name.startsWith('points')) throw new Error('Unexpected heavy request');
    const body = files.get(name)!;
    const bytes = typeof body === 'string' ? new TextEncoder().encode(body) : body;
    const range = (options?.headers as Record<string, string>)?.Range;
    if (range) {
      const [start, end] = range.slice(6).split('-').map(Number);
      return new Response(bytes.slice(start, end + 1), {status: 206, headers: {'Content-Range': `bytes ${start}-${Math.min(end, bytes.length - 1)}/${bytes.length}`}});
    }
    return new Response(bytes);
  }));
  const p = await loadColmap4dPayload('/model', () => {}, 3, 'cameras');
  expect(p.imageNames).toEqual(['a.png']);
  expect(p.shown).toBe(0);
  expect(p.files.map(f => f.name)).toEqual(['cameras.bin', 'images.bin', 'points3D.bin', 'times.bin', 'time_meta.json']);
});
test('point stage requests no camera tables and keeps sampling independent', async () => {
  const files = binaryFiles();
  for (const name of ['cameras.bin', 'images.bin', 'times.bin', 'time_meta.json']) files.delete(name);
  serve(files);
  const p = await loadColmap4dPayload('/model', () => {}, 3, 'points');
  expect(p.files.map(f => f.name)).toEqual(['points3D.bin', 'points_t.bin']);
  expect([p.total, p.shown]).toEqual([7, 3]);
});
test('camera stage refuses an ignored Range without buffering the heavy image table', async () => {
  const heavy = new Response('not a bounded response');
  const read = vi.spyOn(heavy, 'arrayBuffer');
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.endsWith('cameras.bin') ? new Response(u64(0)) : heavy));
  await expect(loadColmap4dPayload('/model', () => {}, 3, 'cameras')).rejects.toThrow('requires HTTP Range');
  expect(read).not.toHaveBeenCalled();
});
