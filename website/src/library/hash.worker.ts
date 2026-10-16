import { createSHA256 } from 'hash-wasm';
self.onmessage = async ({ data: file }: MessageEvent<File>) => {
  try {
    const hash = await createSHA256();
    for (let offset = 0; offset < file.size; offset += 8 * 1024 ** 2) {
      hash.update(new Uint8Array(await file.slice(offset, offset + 8 * 1024 ** 2).arrayBuffer()));
      self.postMessage({ progress: Math.min(file.size, offset + 8 * 1024 ** 2) });
    }
    self.postMessage({ digest: hash.digest('hex') });
  } catch (error) { self.postMessage({ error: String(error) }); }
};
