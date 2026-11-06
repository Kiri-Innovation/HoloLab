/** HTTP/Blob bridge only. Source decoding, sampling and stat live in colmap4d.preview. */
import { colmap4dUrl } from './colmap4dUrls';

export interface ComponentSizes {
  cameras_bytes: number;
  points_bytes: number;
  images_bytes: number | null;
  points_geometry_bytes: number;
  points_time_bytes: number;
  image_size_error?: string | null;
}
interface WirePreview {
  files: {name: string; base64: string}[];
  total: number;
  shown: number;
  image_names: string[];
  estimated: boolean;
  camera_bounds: {center: number[]; radius: number} | null;
  next_offset: number | null;
}
export function colmap4dRequestUrl(proxyUrl: string, part: 'metadata' | 'cameras' | 'points') {
  const path = colmap4dUrl(proxyUrl);
  const match = /^\/proxy\/([^/]+)\/(.+)$/.exec(path);
  if (!match) throw new Error('Expected handle proxy_url with a directory');
  return `/proxy/${match[1]}/_format/colmap4d/${match[2]}?part=${part}`;
}
async function request<T>(proxyUrl: string, part: 'metadata' | 'cameras' | 'points', signal: AbortSignal): Promise<T> {
  const r = await fetch(colmap4dRequestUrl(proxyUrl, part), {signal});
  if (!r.ok) {
    const detail = await r.json().then(data => data.detail).catch(() => '');
    throw new Error(`预览接口 HTTP ${r.status}: ${detail || '请确认计算节点已安装 colmap4d>=1.0.5 并更新预览服务'}`);
  }
  const data = await r.json();
  if (data.format !== 'colmap4d' || data.part !== part) throw new Error('Invalid preview response');
  return data.result;
}
export function loadColmap4dMetadata(proxyUrl: string, signal: AbortSignal) {
  return request<ComponentSizes>(proxyUrl, 'metadata', signal);
}
export async function loadColmap4dPayload(proxyUrl: string, part: 'cameras' | 'points', signal: AbortSignal) {
  const p = await request<WirePreview>(proxyUrl, part, signal);
  return {
    files: p.files.map(file => ({name: file.name, blob: new Blob([Uint8Array.from(atob(file.base64), char => char.charCodeAt(0))])})),
    total: p.total, shown: p.shown, imageNames: p.image_names, estimated: p.estimated,
    cameraBounds: p.camera_bounds, nextOffset: p.next_offset,
  };
}
