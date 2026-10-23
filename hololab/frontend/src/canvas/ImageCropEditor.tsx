import { useEffect, useRef, useState } from 'react';
import { useStore } from '@xyflow/react';
import { getHandleSummary } from '../api';
import type { AlgorithmNodeData } from './AlgorithmNode';
import { useCanvasContext } from './CanvasContext';
import { cropError, cropRect, cropViewport, dragCrop, padKeys, type Pads, type CropHandle, type CropRect } from './imageCropGeometry';
import './imageCropEditor.css';
import { storedPixelBlob } from './imageCropPixels';

export function ImageCropEditor({ id, readOnly = false }: { id: string; readOnly?: boolean }) {
  const nodes = useStore(s => s.nodes), edges = useStore(s => s.edges);
  const { onParamsChange } = useCanvasContext();
  const self = nodes.find(n => n.id === id)?.data as AlgorithmNodeData | undefined;
  const edge = edges.find(e => e.target === id && e.targetHandle === 'image');
  const source = nodes.find(n => n.id === edge?.source)?.data as AlgorithmNodeData | undefined;
  const handle = (edge?.data?.handleId as string | undefined) ?? source?.previews?.[edge?.sourceHandle ?? '']?.handle_id;
  const pads = Object.fromEntries(padKeys.map(k => [k, Number(self?.params?.[k] ?? self?.pack.params[k]?.default ?? 0)])) as Pads;
  const paramsKey = JSON.stringify(pads);
  const [media, setMedia] = useState<{ handle: string; bitmap: ImageBitmap }>();
  const [loadError, setLoadError] = useState('');
  const [draft, setDraft] = useState<Pads | null>(null);
  const canvas = useRef<HTMLCanvasElement>(null), stage = useRef<HTMLDivElement>(null);
  const gesture = useRef<{ start: Pads; handle: CropHandle; x: number; y: number; sx: number; sy: number; view: CropRect; pending?: Pads }>();
  const writable = !readOnly && !!onParamsChange;
  useEffect(() => {
    let cancelled = false;
    let bitmap: ImageBitmap | undefined;
    setMedia(undefined); setLoadError('');
    if (handle) getHandleSummary(handle).then(async summary => {
      if (summary.storage !== 'file') throw Error('请输入单张图像；数组请先选择单个元素');
      const response = await fetch(summary.proxy_url);
      if (!response.ok) throw Error('无法读取输入图像');
      // Stored pixels, exactly as crop.py: do not apply EXIF orientation.
      bitmap = await createImageBitmap(storedPixelBlob(new Uint8Array(await response.arrayBuffer())));
      if (cancelled) bitmap.close();
      else setMedia({ handle, bitmap });
    }).catch(e => { if (!cancelled) setLoadError(String(e.message ?? e)); });
    return () => { cancelled = true; bitmap?.close(); };
  }, [handle]);
  useEffect(() => { gesture.current = undefined; setDraft(null); }, [paramsKey, handle, readOnly]);
  const bitmap = media?.handle === handle ? media?.bitmap : undefined;
  const w = bitmap?.width ?? 0, h = bitmap?.height ?? 0;
  useEffect(() => {
    if (bitmap && canvas.current) canvas.current.getContext('2d')?.drawImage(bitmap, 0, 0);
  }, [bitmap]);
  const value = draft ?? pads;
  const error = w && h ? cropError(w, h, value) : null;
  const rect = cropRect(w, h, value);
  const view = gesture.current?.view ?? cropViewport(w || 1, h || 1, error ? { pad_left: 0, pad_top: 0, pad_right: 0, pad_bottom: 0 } : value);
  const style = (r: CropRect) => ({ left: `${(r.x - view.x) / view.width * 100}%`, top: `${(r.y - view.y) / view.height * 100}%`, width: `${r.width / view.width * 100}%`, height: `${r.height / view.height * 100}%` });
  function start(e: React.PointerEvent, action: CropHandle) {
    e.stopPropagation();
    if (!writable || error || !stage.current || e.button !== 0) return;
    e.preventDefault();
    e.currentTarget.closest<HTMLElement>('section')?.focus({ preventScroll: true });
    e.currentTarget.setPointerCapture(e.pointerId);
    const bounds = stage.current.getBoundingClientRect();
    gesture.current = { start: pads, handle: action, x: e.clientX, y: e.clientY, sx: view.width / bounds.width, sy: view.height / bounds.height, view };
  }
  function move(e: React.PointerEvent) {
    const g = gesture.current;
    if (!g || !writable) return;
    g.pending = dragCrop(w, h, g.start, g.handle, (e.clientX - g.x) * g.sx, (e.clientY - g.y) * g.sy);
    setDraft(g.pending);
  }
  function end(cancel = false) {
    const next = gesture.current?.pending;
    gesture.current = undefined; setDraft(null);
    if (!cancel && writable && next) onParamsChange?.(id, next);
  }
  return <section tabIndex={-1} className="hl-image-crop-editor nodrag nopan nowheel" aria-label="Image crop editor" onPointerDown={e => e.stopPropagation()} onKeyDown={e => { e.stopPropagation(); if (e.key === 'Escape') end(true); }}>
    <header><strong>Image crop editor</strong><span>{readOnly ? '历史只读' : '拖动裁剪框 / 八向缩放'}</span></header>
    <small>使用上游最近产物 · 左上角为原点 · 正数向内裁，负数向外扩（px）</small>
    <div role="status">{!handle ? '请连接 image 并运行上游；可先编辑数值' : loadError ? `${loadError}；可编辑数值` : !bitmap ? '正在读取图像尺寸；可先编辑数值' : error ?? `${w} × ${h} → ${rect.width} × ${rect.height} px`}</div>
    {bitmap && <div ref={stage} className="hl-image-crop-stage" style={{ aspectRatio: `${view.width}/${view.height}` }}>
      <canvas ref={canvas} width={w} height={h} style={style({ x: 0, y: 0, width: w, height: h })} aria-label="Input image (stored pixels)" />
      {!error && <div className="hl-image-crop-selection" data-readonly={!writable} style={style(rect)} aria-label="Crop rectangle" onPointerDown={e => start(e, 'move')} onPointerMove={move} onPointerUp={() => end()} onPointerCancel={() => end(true)} onLostPointerCapture={() => end(true)}>
        {(['n', 'ne', 'e', 'se', 's', 'sw', 'w', 'nw'] as const).map(side => <button key={side} className={`hl-image-crop-handle hl-image-crop-${side}`} aria-label={`Resize crop ${side}`} disabled={!writable} onPointerDown={e => start(e, side)} />)}
      </div>}
    </div>}
    <div className="hl-image-crop-values">{padKeys.map(k => <label key={k}>{k}<input aria-label={`Crop ${k}`} type="number" step="any" value={value[k]} disabled={!writable} onChange={e => { const n = e.currentTarget.valueAsNumber; if (Number.isFinite(n)) onParamsChange?.(id, { [k]: n }); }} /></label>)}</div>
    <small>拖动松手后保存；修改标记 drift，不自动运行。小数按半像素远离零取整。</small>
  </section>;
}
