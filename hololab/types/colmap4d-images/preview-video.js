// Offline, in-memory VP8/WebM. Canvas WebP supplies VP8 keyframes, so encoding
// does not depend on real-time capture or secure-context-only WebCodecs.
// Container references: webmproject.org/docs/container/ and
// developers.google.com/speed/webp/docs/riff_container .
const text = value => new TextEncoder().encode(value);
function uint(value, width = Math.max(1, Math.ceil(Math.log2(value + 1) / 8))) {
  const bytes = new Uint8Array(width);
  for (let i = width - 1; i >= 0; i--) { bytes[i] = value % 256; value = Math.floor(value / 256); }
  return bytes;
}
function element(id, payload) {
  const body = payload instanceof Blob ? payload : new Blob(Array.isArray(payload) ? payload : [payload]);
  let width = 1;
  while (body.size >= 2 ** (7 * width) - 1) width++;
  const size = uint(body.size, width); size[0] |= 1 << (8 - width);
  return new Blob([uint(id), size, body]);
}
const number = (id, value) => element(id, uint(value));
function float(value) { const bytes = new Uint8Array(8); new DataView(bytes.buffer).setFloat64(0, value); return bytes; }

export function vp8Frame(buffer) {
  const bytes = new Uint8Array(buffer), view = new DataView(buffer);
  const fourcc = offset => String.fromCharCode(...bytes.subarray(offset, offset + 4));
  if (fourcc(0) !== 'RIFF' || fourcc(8) !== 'WEBP') throw Error('This browser cannot encode WebP previews');
  for (let offset = 12; offset + 8 <= bytes.length;) {
    const size = view.getUint32(offset + 4, true), end = offset + 8 + size;
    if (end > bytes.length) break;
    if (fourcc(offset) === 'VP8 ') return bytes.slice(offset + 8, end);
    offset = end + (size % 2);
  }
  throw Error('This browser cannot encode VP8 previews');
}

export function webm(frames, width, height, fps) {
  if (!frames.length) throw Error('No frames to encode');
  const header = element(0x1a45dfa3, [number(0x4286, 1), number(0x42f7, 1), number(0x42f2, 4),
    number(0x42f3, 8), element(0x4282, text('webm')), number(0x4287, 2), number(0x4285, 2)]);
  const info = element(0x1549a966, [number(0x2ad7b1, 1000000),
    element(0x4489, float(frames.length * 1000 / fps)),
    element(0x4d80, text('HoloLab')), element(0x5741, text('HoloLab'))]);
  const tracks = element(0x1654ae6b, [element(0xae, [number(0xd7, 1), number(0x73c5, 1),
    number(0x83, 1), element(0x86, text('V_VP8')), number(0x23e383, Math.round(1e9 / fps)),
    element(0xe0, [number(0xb0, width), number(0xba, height)])])]);
  const clusters = [], starts = [];
  let blocks = [], start = 0;
  for (let i = 0; i < frames.length; i++) {
    const time = Math.round(i * 1000 / fps);
    if (blocks.length && time - start >= 1000) {
      clusters.push(element(0x1f43b675, [number(0xe7, start), ...blocks])); starts.push(start);
      blocks = []; start = time;
    }
    const relative = time - start;
    blocks.push(element(0xa3, new Blob([new Uint8Array([0x81, relative >> 8, relative & 255, 0x80]), frames[i]])));
  }
  clusters.push(element(0x1f43b675, [number(0xe7, start), ...blocks])); starts.push(start);
  // Put cues before clusters for immediate seeking. Fixed-width offsets keep
  // the cue table size unchanged when its final positions are filled in.
  function cues(first) {
    let offset = first;
    return element(0x1c53bb6b, clusters.map((cluster, i) => {
      const cue = element(0xbb, [number(0xb3, starts[i]), element(0xb7, [number(0xf7, 1),
        element(0xf1, uint(offset, 8))])]); offset += cluster.size; return cue;
    }));
  }
  const table = cues(info.size + tracks.size + cues(0).size);
  return new Blob([header, element(0x18538067, [info, tracks, table, ...clusters])], {type: 'video/webm'});
}

export function thumbnailUrl(root, name) {
  const url = new URL(root, location.origin), parts = url.pathname.split('/').filter(Boolean);
  const path = [...parts.slice(2), ...name.split('/').map(encodeURIComponent)].join('/');
  return `/proxy/${parts[1]}/_thumb/640x480/${path}?at=0&unpadded=true`;
}

export async function synthesize(frames, urlFor, fps, parentSignal, progress, originalUrlFor) {
  const controller = new AbortController(), signal = controller.signal;
  const cancel = () => controller.abort();
  parentSignal.addEventListener('abort', cancel, {once: true});
  if (parentSignal.aborted) cancel();
  const canvas = document.createElement('canvas'), context = canvas.getContext('2d', {alpha: false});
  const encoded = [];
  let useOriginal = false;
  const pending = new Map();
  async function load(index) {
    let response = await fetch((useOriginal ? originalUrlFor : urlFor)(frames[index].name), {signal});
    if (!response.ok && originalUrlFor) {
      useOriginal = true;
      response = await fetch(originalUrlFor(frames[index].name), {signal});
    }
    if (!response.ok) throw Error(`${frames[index].name}: HTTP ${response.status}`);
    return response.blob();
  }
  try {
    for (let i = 0; i < frames.length; i++) {
      signal.throwIfAborted();
      // Pipeline up to four compressed thumbnails, but decode only one image.
      // This overlaps HTTP/transcode latency without a decoded-frame cache.
      for (let n = i; n < Math.min(frames.length, i + 4); n++) if (!pending.has(n)) {
        const promise = load(n); promise.catch(() => {}); pending.set(n, promise);
      }
      const blob = await pending.get(i); pending.delete(i);
      signal.throwIfAborted();
      const image = await createImageBitmap(blob);
      try {
        signal.throwIfAborted();
        if (!i) {
          const scale = Math.min(1, 640 / Math.max(image.width, image.height));
          canvas.width = Math.max(1, Math.round(image.width * scale));
          canvas.height = Math.max(1, Math.round(image.height * scale));
        }
        context.fillStyle = '#000'; context.fillRect(0, 0, canvas.width, canvas.height);
        const scale = Math.min(canvas.width / image.width, canvas.height / image.height);
        const w = image.width * scale, h = image.height * scale;
        context.drawImage(image, (canvas.width - w) / 2, (canvas.height - h) / 2, w, h);
      } finally { image.close(); }
      const encodedBlob = await new Promise(resolve => canvas.toBlob(resolve, 'image/webp', 0.85));
      signal.throwIfAborted();
      if (!encodedBlob) throw Error('Could not encode preview frame');
      encoded.push(vp8Frame(await encodedBlob.arrayBuffer()));
      progress(i + 1, frames.length);
    }
    signal.throwIfAborted();
    return webm(encoded, canvas.width, canvas.height, fps);
  } finally { controller.abort(); parentSignal.removeEventListener('abort', cancel); pending.clear(); canvas.width = canvas.height = 1; }
}
