/** Strip EXIF from a display-only copy. Chromium applies EXIF even with
 * createImageBitmap({imageOrientation:'none'}); crop.py uses stored pixels.
 * Preserve image samples and all other chunks, including colour profiles. */
export function storedPixelBlob(bytes: Uint8Array): Blob {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const parts: Uint8Array[] = [];
  const text = (a: number, b: number) => String.fromCharCode(...bytes.subarray(a, b));
  if (bytes[0] === 0xff && bytes[1] === 0xd8) {
    parts.push(bytes.slice(0, 2));
    let pos = 2;
    while (pos + 4 <= bytes.length && bytes[pos] === 0xff) {
      const marker = bytes[pos + 1];
      if (marker === 0xda || marker === 0xd9) break; // compressed scan / end
      const end = pos + 2 + view.getUint16(pos + 2);
      if (end <= pos + 2 || end > bytes.length) throw Error('JPEG 元数据损坏');
      if (!(marker === 0xe1 && text(pos + 4, pos + 10) === 'Exif\0\0')) parts.push(bytes.slice(pos, end));
      pos = end;
    }
    parts.push(bytes.slice(pos));
    return new Blob(parts as BlobPart[], {type:'image/jpeg'});
  }
  if (bytes[0] === 137 && text(1, 4) === 'PNG') {
    parts.push(bytes.slice(0, 8));
    for (let pos = 8; pos < bytes.length;) {
      if (pos + 12 > bytes.length) throw Error('PNG 元数据损坏');
      const end = pos + 12 + view.getUint32(pos);
      if (end > bytes.length) throw Error('PNG 元数据损坏');
      if (text(pos + 4, pos + 8) !== 'eXIf') parts.push(bytes.slice(pos, end));
      pos = end;
    }
    return new Blob(parts as BlobPart[], {type:'image/png'});
  }
  if (text(0, 4) === 'RIFF' && text(8, 12) === 'WEBP') {
    const header = bytes.slice(0, 12); parts.push(header);
    let size = 12;
    for (let pos = 12; pos < bytes.length;) {
      if (pos + 8 > bytes.length) throw Error('WebP 元数据损坏');
      const length = view.getUint32(pos + 4, true), end = pos + 8 + length + (length % 2);
      if (end > bytes.length) throw Error('WebP 元数据损坏');
      if (text(pos, pos + 4) !== 'EXIF') {
        const chunk = bytes.slice(pos, end);
        if (text(pos, pos + 4) === 'VP8X') chunk[8] &= ~8;
        parts.push(chunk); size += chunk.length;
      }
      pos = end;
    }
    new DataView(header.buffer).setUint32(4, size - 8, true);
    return new Blob(parts as BlobPart[], {type:'image/webp'});
  }
  throw Error('输入须为 PNG、JPEG 或 WebP');
}
