import { useEffect, useRef, useState } from "react";
import { useStore } from "@xyflow/react";
import { getHandleSummary } from "../api";
import type { AlgorithmNodeData } from "./AlgorithmNode";
import { useCanvasContext } from "./CanvasContext";
import {
  activeAt,
  compositionError,
  layerRect,
  moveTransform,
  resizeTransform,
  type Clip,
  type Transform,
} from "./composeGeometry";
import "./composeVideoEditor.css";
import { repairComposeValue } from "./composeRepairs";

/** Uses the connected handle, never a hard-coded output.mp4 from a previous compose run. */
export function ComposeVideoEditor({
  id,
  readOnly = false,
}: {
  id: string;
  readOnly?: boolean;
}) {
  const nodes = useStore((s) => s.nodes);
  const edges = useStore((s) => s.edges);
  const { onInlineValueChange, onParamsChange } = useCanvasContext();
  const self = nodes.find((n) => n.id === id)?.data as
    AlgorithmNodeData | undefined;
  const incoming = (port: string) =>
    edges.find((e) => e.target === id && e.targetHandle === port);
  const videoEdge = incoming("videos");
  const producer = nodes.find((n) => n.id === videoEdge?.source)?.data as
    AlgorithmNodeData | undefined;
  const handle =
    (videoEdge?.data?.handleId as string | undefined) ??
    producer?.previews?.[videoEdge?.sourceHandle ?? ""]?.handle_id;
  const [clips, setClips] = useState<Clip[]>([]);
  const [loadedHandle, setLoadedHandle] = useState<string>();
  const [repairs, setRepairs] = useState<string[]>([]);
  const lastRepair = useRef("");
  const [loadError, setLoadError] = useState("");
  const [time, setTime] = useState(0);
  const [selected, setSelected] = useState(0);
  const [draft, setDraft] = useState<Transform[] | null>(null);
  const stage = useRef<HTMLDivElement>(null);
  const videos = useRef(new Map<number, HTMLVideoElement>());
  const gesture = useRef<{
    index: number;
    start: Transform[];
    x: number;
    y: number;
    w: number;
    h: number;
    resize: boolean;
    rw: number;
    rh: number;
  } | null>(null);
  const pending = useRef<Transform[] | null>(null);
  const binding = (port: "transforms" | "offsets") => {
    const edge = incoming(port);
    const source = nodes.find((n) => n.id === edge?.source)?.data as
      AlgorithmNodeData | undefined;
    const expected =
      port === "transforms"
        ? "@builtin/constant-vec3-array"
        : "@builtin/constant-float-array";
    const constant = source?.pack?.name === expected;
    const p = source?.params ?? {};
    const value = edge
      ? constant
        ? port === "offsets"
          ? p.items
          : [p.x, p.y, p.scale].some(Array.isArray)
            ? Array.from(
                {
                  length: Math.max(
                    ...[p.x, p.y, p.scale].map((column) =>
                      Array.isArray(column) ? column.length : 0,
                    ),
                  ),
                },
                (_, i) =>
                  [p.x, p.y, p.scale].map((column) =>
                    Array.isArray(column) ? column[i] : undefined,
                  ),
              )
            : null
        : null
      : (self?.port_values?.[port] ??
        (readOnly ? undefined : self?.pack.inputs[port]?.inline_default));
    return {
      value,
      writable:
        !readOnly &&
        (edge ? constant && !!onParamsChange : !!onInlineValueChange),
      label: edge
        ? constant
          ? `常量 ${edge.source}（修改影响所有消费者）`
          : "算法输出：请在上游编辑"
        : "节点内联值",
      write: (value: unknown) => {
        if (readOnly) return;
        if (!edge) onInlineValueChange?.(id, port, value);
        else if (constant) {
          if (port === "offsets")
            onParamsChange?.(edge.source, { items: value });
          else {
            const rows = value as Transform[];
            onParamsChange?.(edge.source, {
              x: rows.map((t) => t[0]),
              y: rows.map((t) => t[1]),
              scale: rows.map((t) => t[2]),
            });
          }
        }
      },
    };
  };
  const transforms = binding("transforms"),
    offsets = binding("offsets");
  useEffect(() => {
    let cancelled = false;
    setClips([]);
    setLoadError("");
    setTime(0);
    setSelected(0);
    setDraft(null);
    if (!handle) return;
    getHandleSummary(handle)
      .then((summary) => {
        if (summary.fields.truncated)
          throw Error("素材目录摘要不完整，不能可靠重建层顺序");
        const entries = (summary.fields.entries ?? [])
          .filter((e) => !e.name.startsWith("."))
          .sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
        const rows = entries.map((e) => {
          const children = e.is_dir
            ? (e.children ?? []).filter(
                (c) =>
                  !c.is_dir && /\.(mp4|mov|mkv|webm|avi|m4v)$/i.test(c.name),
              )
            : [];
          if (
            e.is_dir &&
            e.entry_count != null &&
            e.entry_count > (e.children?.length ?? 0)
          )
            throw Error(`素材 ${e.name} 目录摘要不完整，不能可靠选取视频`);
          if (e.is_dir && children.length !== 1)
            throw Error(`素材 ${e.name} 未找到唯一视频；请先运行素材上游`);
          const path = e.is_dir ? `${e.name}/${children[0].name}` : e.name;
          return {
            id: e.name,
            url:
              summary.proxy_url.replace(/\/$/, "") +
              "/" +
              path.split("/").map(encodeURIComponent).join("/"),
            width: 0,
            height: 0,
            duration: 0,
          };
        });
        if (!rows.length) throw Error("视频数组为空");
        if (!cancelled) {
          setClips(rows);
          setLoadedHandle(handle);
        }
      })
      .catch((e) => {
        if (!cancelled) setLoadError(String(e.message ?? e));
      });
    return () => {
      cancelled = true;
    };
  }, [handle]);
  const ready =
    loadedHandle === handle &&
    clips.length > 0 &&
    clips.every((c) => c.width > 0 && c.height > 0 && c.duration > 0);
  // Compare source values, not object identity; constant bindings are reconstructed each render.
  useEffect(() => {
    if (!ready || readOnly) return;
    const plans = (
      [
        ["transforms", transforms],
        ["offsets", offsets],
      ] as const
    )
      .filter(([, target]) => target.writable)
      .map(([port, target]) => ({
        port,
        target,
        plan: repairComposeValue(port, target.value, clips.length),
      }))
      .filter(({ plan }) => plan.changes.length);
    if (!plans.length) {
      lastRepair.current = "";
      return;
    }
    const key = JSON.stringify([
      handle,
      plans.map(({ port, target, plan }) => [port, target.label, plan.changes]),
    ]);
    if (lastRepair.current === key) return;
    lastRepair.current = key;
    setRepairs(
      plans.flatMap(({ target, plan }) =>
        plan.changes.map((change) => `${change}（${target.label}）`),
      ),
    );
    for (const { target, plan } of plans) target.write(plan.value);
  });
  const error = ready
    ? (transforms.value == null && incoming("transforms")) ||
      (offsets.value == null && incoming("offsets"))
      ? "此编辑器支持内联值或直接连接的内建常量；其它属性生产者请在上游编辑，并查看运行产物。"
      : compositionError(transforms.value, offsets.value, clips.length)
    : null;
  const ts = (draft ?? transforms.value ?? []) as Transform[];
  const os = (offsets.value ?? []) as number[];
  const valid = ready && !error;
  const canvas = {
    width: Math.ceil((clips[0]?.width || 2) / 2) * 2,
    height: Math.ceil((clips[0]?.height || 2) / 2) * 2,
  };
  const total = valid
    ? Math.max(...clips.map((c, i) => os[i] + c.duration))
    : 0;
  const now = Math.min(time, total);
  useEffect(() => {
    if (!valid) return;
    const frame = requestAnimationFrame(() => {
      videos.current.forEach((video, i) => {
        video.pause();
        if (
          activeAt(now, os[i], clips[i].duration) &&
          Math.abs(video.currentTime - (now - os[i])) > 0.015
        )
          video.currentTime = now - os[i];
      });
    });
    return () => cancelAnimationFrame(frame);
  }, [now, valid, clips, offsets.value]);
  function start(e: React.PointerEvent, index: number, resize: boolean) {
    e.stopPropagation();
    setSelected(index);
    if (index === 0 || !valid || !transforms.writable || !stage.current) return;
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    const box = stage.current.getBoundingClientRect(),
      rect = layerRect(clips[index], canvas, ts[index]);
    gesture.current = {
      index,
      start: ts.map((t) => [...t] as Transform),
      x: e.clientX,
      y: e.clientY,
      w: box.width,
      h: box.height,
      resize,
      rw: (rect.width / canvas.width) * box.width,
      rh: (rect.height / canvas.height) * box.height,
    };
  }
  function move(e: React.PointerEvent) {
    const g = gesture.current;
    if (!g) return;
    const next = g.start.map((t) => [...t] as Transform);
    next[g.index] = g.resize
      ? resizeTransform(
          g.start[g.index],
          e.clientX - g.x,
          e.clientY - g.y,
          g.rw,
          g.rh,
        )
      : moveTransform(
          g.start[g.index],
          e.clientX - g.x,
          e.clientY - g.y,
          g.w,
          g.h,
        );
    pending.current = next;
    setDraft(next);
  }
  function end(cancel = false) {
    if (!cancel && pending.current) transforms.write(pending.current);
    pending.current = null;
    gesture.current = null;
    setDraft(null);
  }
  return (
    <section
      className="hl-compose-editor nodrag nopan nowheel"
      aria-label="Composition editor"
      onPointerDown={(e) => e.stopPropagation()}
      onKeyDown={(e) => e.stopPropagation()}
    >
      <header>
        <strong>Composition editor</strong>
        <span>{readOnly ? "历史只读" : "拖动 / 右下角缩放"}</span>
      </header>
      <small>使用上游最近产物；修改素材后请先重跑上游。</small>
      <div role="status">
        {!handle
          ? "请连接 videos 并运行素材上游"
          : loadError ||
            error ||
            (!ready
              ? "正在读取视频元数据…"
              : `${canvas.width} × ${canvas.height} · ${total.toFixed(3)}s`)}
      </div>
      {repairs.length > 0 && (
        <aside
          className="hl-compose-repairs"
          aria-label="Automatic corrections"
          aria-live="polite"
        >
          <details open>
            <summary>已自动修正 {repairs.length} 处</summary>
            <ul>
              {repairs.map((change, i) => (
                <li key={i}>{change}</li>
              ))}
            </ul>
          </details>
          <button
            onClick={() => setRepairs([])}
            aria-label="Dismiss corrections"
          >
            关闭提示
          </button>
        </aside>
      )}
      <div
        ref={stage}
        className="hl-compose-stage"
        style={{ aspectRatio: `${canvas.width}/${canvas.height}` }}
      >
        {clips.map((clip, i) => {
          const visible = valid && activeAt(now, os[i], clip.duration);
          const rect = valid
            ? layerRect(clip, canvas, ts[i])
            : { x: 0, y: 0, width: 0, height: 0 };
          return (
            <div
              key={clip.url}
              className="hl-compose-layer"
              data-layer={i}
              data-active={visible}
              data-selected={selected === i}
              style={{
                left: `${(rect.x / canvas.width) * 100}%`,
                top: `${(rect.y / canvas.height) * 100}%`,
                width: `${(rect.width / canvas.width) * 100}%`,
                height: `${(rect.height / canvas.height) * 100}%`,
                visibility: visible ? "visible" : "hidden",
                zIndex: i,
                cursor: i === 0 ? "default" : undefined,
              }}
              onPointerDown={(e) => start(e, i, false)}
              onPointerMove={move}
              onPointerUp={() => end()}
              onPointerCancel={() => end(true)}
            >
              <video
                muted
                playsInline
                preload="metadata"
                src={clip.url}
                ref={(el) => {
                  if (el) videos.current.set(i, el);
                  else videos.current.delete(i);
                }}
                onLoadedMetadata={(e) => {
                  const v = e.currentTarget;
                  if (!Number.isFinite(v.duration) || v.duration <= 0) {
                    setLoadError(`素材 ${clip.id} 时长无效`);
                    return;
                  }
                  setClips((old) =>
                    old.map((c) =>
                      c.url === clip.url
                        ? {
                            ...c,
                            width: v.videoWidth,
                            height: v.videoHeight,
                            duration: v.duration,
                          }
                        : c,
                    ),
                  );
                }}
                onError={() => setLoadError(`无法读取素材 ${clip.id}`)}
              />
            </div>
          );
        })}
        {valid &&
          activeAt(now, os[selected], clips[selected].duration) &&
          (() => {
            const rect = layerRect(clips[selected], canvas, ts[selected]);
            return (
              <div
                className="hl-compose-selection"
                style={{
                  left: `${(rect.x / canvas.width) * 100}%`,
                  top: `${(rect.y / canvas.height) * 100}%`,
                  width: `${(rect.width / canvas.width) * 100}%`,
                  height: `${(rect.height / canvas.height) * 100}%`,
                  zIndex: clips.length,
                }}
                onPointerMove={move}
                onPointerUp={() => end()}
                onPointerCancel={() => end(true)}
              >
                {selected !== 0 && transforms.writable && (
                  <>
                    <button
                      className="hl-compose-move"
                      aria-label={`Move layer ${selected + 1}`}
                      title="移动所选层"
                      onPointerDown={(e) => start(e, selected, false)}
                    >
                      ↔
                    </button>
                    <button
                      className="hl-compose-resize"
                      aria-label={`Resize layer ${selected + 1}`}
                      onPointerDown={(e) => start(e, selected, true)}
                    />
                  </>
                )}
              </div>
            );
          })()}
      </div>
      <label className="hl-compose-scrub">
        时间 {now.toFixed(3)} / {total.toFixed(3)} s
        <input
          aria-label="Composition time"
          type="range"
          min="0"
          max={total || 1}
          step="0.001"
          value={now}
          disabled={!valid}
          onChange={(e) => setTime(Number(e.target.value))}
        />
      </label>
      {valid && (
        <div className="hl-compose-tracks">
          {clips.map((c, i) => (
            <button
              key={c.id}
              aria-label={`Select layer ${i + 1}`}
              aria-pressed={selected === i}
              onClick={() => {
                setSelected(i);
                setTime(os[i] + Math.min(0.01, c.duration / 2));
              }}
            >
              <span>
                {i + 1} · {c.id}
              </span>
              <span
                className="hl-compose-track"
                title={`${os[i].toFixed(3)}–${(os[i] + c.duration).toFixed(3)} s`}
              >
                <i
                  style={{
                    left: `${(os[i] / total) * 100}%`,
                    width: `${(c.duration / total) * 100}%`,
                  }}
                />
              </span>
            </button>
          ))}
        </div>
      )}
      {valid && (
        <div className="hl-compose-properties">
          <span>
            层 {selected + 1} ·{" "}
            {activeAt(now, os[selected], clips[selected].duration)
              ? "可见"
              : "此刻隐藏"}
          </span>
          {selected === 0 && <small>底层 · 决定画布尺寸，位置与缩放固定为 [0,0,1]</small>}
          <small>{transforms.label}</small>
          <label>
            开始时间{" "}
            <input
              aria-label="Layer offset"
              type="number"
              min="0"
              step="0.1"
              value={os[selected]}
              disabled={!offsets.writable}
              onChange={(e) => {
                const v = e.currentTarget.valueAsNumber;
                if (Number.isFinite(v)) {
                  const next = [...os];
                  next[selected] = v;
                  const plan = repairComposeValue(
                    "offsets",
                    next,
                    clips.length,
                  );
                  if (plan.changes.length)
                    setRepairs(
                      plan.changes.map(
                        (change) => `${change}（${offsets.label}）`,
                      ),
                    );
                  offsets.write(plan.value);
                }
              }}
            />
          </label>
          <small>{offsets.label}</small>
        </div>
      )}
    </section>
  );
}
