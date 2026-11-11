// COLMAP image records are authoritative: rendered names need not be f*__cam*.
const decoder = new TextDecoder();
export function safeName(name) {
  return typeof name === 'string' && name && !name.includes('\\')
    && name.split('/').every(part => part && part !== '.' && part !== '..');
}

export function readImages(buffer) {
  const view = new DataView(buffer), bytes = new Uint8Array(buffer);
  let offset = 0;
  const take = size => {
    const start = offset; offset += size;
    if (!Number.isSafeInteger(offset) || offset > bytes.length) throw Error('Invalid images.bin');
    return start;
  };
  const count = Number(view.getBigUint64(take(8), true)), records = [];
  for (let i = 0; i < count; i++) {
    const start = take(64), id = view.getUint32(start, true), camera = view.getUint32(start + 60, true);
    const end = bytes.indexOf(0, offset);
    if (end < 0) throw Error('Invalid images.bin name');
    const name = decoder.decode(bytes.subarray(offset, end)); offset = end + 1;
    const points = Number(view.getBigUint64(take(8), true)); take(points * 24);
    if (!safeName(name)) throw Error('Invalid image path in images.bin');
    records.push({name: `images/${name}`, frame: id, camera: `cam${camera}`});
  }
  return records;
}

export function readTimes(buffer) {
  const view = new DataView(buffer), times = new Map();
  if (buffer.byteLength < 8) throw Error('Invalid times.bin');
  const count = Number(view.getBigUint64(0, true));
  if (8 + count * 12 !== buffer.byteLength) throw Error('Invalid times.bin');
  for (let i = 0, offset = 8; i < count; i++, offset += 12) {
    times.set(view.getUint32(offset, true), view.getBigInt64(offset + 4, true));
  }
  return times;
}

export function legacyImages(files) {
  return [...new Set(files)].flatMap(name => {
    const match = /^(?:images\/)?f(\d+)__cam(\d+)\.(?:png|jpe?g|webp)$/i.exec(name);
    return match ? [{name, frame: Number(match[1]), camera: `cam${Number(match[2])}`}] : [];
  });
}

export function groupImages(records, times = new Map()) {
  const groups = new Map();
  for (const record of records) {
    if (!groups.has(record.camera)) groups.set(record.camera, []);
    groups.get(record.camera).push(record);
  }
  for (const list of groups.values()) list.sort((a, b) => {
    const ta = times.get(a.frame), tb = times.get(b.frame);
    return ta !== undefined && tb !== undefined && ta !== tb ? (ta < tb ? -1 : 1) : a.frame - b.frame;
  });
  return groups;
}

export function partitions(files) {
  return [...new Set(files.flatMap(name => /^p_[^/]+\//.test(name) && safeName(name)
    ? [name.split('/')[0]] : []))].sort((a, b) => a.localeCompare(b, 'en', {numeric: true}));
}
