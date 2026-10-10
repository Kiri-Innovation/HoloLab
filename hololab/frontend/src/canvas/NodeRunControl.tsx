import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { cancelJob } from "../api";
import { Modal } from "../ui/Modal";
import { showToast } from "../ui/Toast";

export const canStopNode = (state?: string) => ["pending", "assigned", "running", "orphaned"].includes(state ?? "");

/** Cancels the current generation's representative parent, including its live shards. */
export function NodeRunControl({ state, jobId, pending, stale, onRun }: {
  state?: string; jobId?: string; pending: boolean; stale?: boolean; onRun: () => void;
}) {
  const stoppable = canStopNode(state);
  const [confirmJob, setConfirmJob] = useState<string | null>(null);
  const [stopping, setStopping] = useState(false);
  const [acceptedJob, setAcceptedJob] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const lock = useRef(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const busy = pending || stopping || (stoppable && acceptedJob === jobId);
  const disabled = busy || (stoppable && !jobId);
  const label = stopping ? "正在停止" : stoppable && acceptedJob === jobId ? "停止已请求，等待状态同步" : stoppable ? "停止运行" : pending ? "正在提交" : stale ? "重新运行" : "运行";
  const close = () => { if (!lock.current) { setConfirmJob(null); setError(null); trigger.current?.focus(); } };
  useEffect(() => {
    // Never let an old confirmation cancel a newly dispatched generation.
    if (!stopping && (confirmJob !== jobId || !stoppable)) setConfirmJob(null);
  }, [confirmJob, jobId, stoppable, stopping]);
  useEffect(() => { if (!stoppable || acceptedJob !== jobId) setAcceptedJob(null); }, [jobId, stoppable, acceptedJob]);
  const stop = async () => {
    if (lock.current || !confirmJob || confirmJob !== jobId || !stoppable) return;
    lock.current = true; setStopping(true); setError(null);
    try {
      const result = await cancelJob(confirmJob);
      setAcceptedJob(confirmJob); setConfirmJob(null);
      showToast({ tone: "success", message: result.already_terminal ? "任务已结束，无需停止" : "已请求停止；请等待任务状态同步" });
      // Runtime/colour is updated only by the existing gateway job_update path.
      trigger.current?.focus();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally { lock.current = false; setStopping(false); }
  };
  const staleTint = !!stale && !stoppable && !busy;
  return <>
    <button ref={trigger} type="button" className={`hl-node-run-control nodrag${stoppable ? " hl-node-stop-control" : ""}`}
      disabled={disabled} aria-label={label}
      data-tooltip={!jobId && stoppable ? "正在获取任务信息，暂不能停止" : stoppable && !busy ? "停止运行（需确认；不是暂停）" : label}
      onClick={e => { e.stopPropagation(); if (stoppable && jobId) { setError(null); setConfirmJob(jobId); } else if (!disabled) onRun(); }}
      style={{ border: `1px solid ${staleTint ? "var(--warning)" : "var(--border-strong)"}`,
        background: busy ? "var(--surface-3)" : staleTint ? "var(--warning-soft)" : "var(--surface-raised)",
        color: busy ? "var(--text-muted)" : staleTint ? "var(--warning)" : "var(--text)" }}>
      {busy ? <span aria-hidden="true">…</span> : <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
        {stoppable ? <rect x="5" y="5" width="14" height="14" rx="1" /> : <path d="m7 4 14 8-14 8Z" />}
      </svg>}
    </button>
    {confirmJob && createPortal(<div onClick={e=>e.stopPropagation()} onPointerDown={e=>e.stopPropagation()} onKeyDown={e=>{
      if(e.key !== "Tab") return;
      const controls=Array.from(e.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)'));
      const first=controls[0],last=controls[controls.length-1];
      if(e.shiftKey && document.activeElement===first){e.preventDefault();last?.focus();}
      else if(!e.shiftKey && document.activeElement===last){e.preventDefault();first?.focus();}
    }}><Modal open onClose={close} title="停止此节点运行？" maxWidth={420}>
      <div className="hl-node-stop-confirm">
        <p>将停止此节点当前任务及其仍在执行的分片。这不是暂停；未完成部分可能需要重新运行。</p>
        {state === "orphaned" && <p>执行设备已失联，停止指令可能暂时无法送达。请检查连接，避免重复运行。</p>}
        {error && <p role="alert" className="hl-node-stop-error">停止失败：{error}</p>}
        <div className="hl-node-stop-confirm-actions">
          <button type="button" autoFocus disabled={stopping} onClick={close}>继续运行</button>
          <button type="button" className="hl-node-stop-submit" disabled={stopping} onClick={stop}>{stopping ? "停止中…" : "确认停止"}</button>
        </div>
      </div>
    </Modal></div>,document.body)}
  </>;
}
