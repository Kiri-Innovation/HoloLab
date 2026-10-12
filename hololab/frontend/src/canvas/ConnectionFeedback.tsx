import { useCallback, useEffect, useRef } from 'react';
import { useConnection, type Connection, type OnConnectEnd } from '@xyflow/react';
import { showConnectionTooltip, type ConnectionTooltip } from '../ui/connectionTooltip';
import type { ConnectionVerdict } from './connectionValidation';

type Endpoint = { nodeId: string; id?: string | null; type: 'source' | 'target' };

export function useConnectionFeedback(validate: (c: Connection) => ConnectionVerdict): { onConnectEnd: OnConnectEnd; recordVerdict: (verdict: ConnectionVerdict) => void } {
  // Subscribe to endpoint changes, not every mouse position. No network requests.
  const connection = useConnection(c => ({
    inProgress: c.inProgress,
    fromNode: c.fromHandle?.nodeId, fromId: c.fromHandle?.id, fromType: c.fromHandle?.type,
    toNode: c.toHandle?.nodeId, toId: c.toHandle?.id, toType: c.toHandle?.type,
  }));
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const dismissed = useRef(false);
  const ended = useRef(true);
  const committed = useRef<ConnectionVerdict | null>(null);
  useEffect(() => {
    const clear = () => {
      dismissed.current = true;
      clearTimeout(timer.current);
      showConnectionTooltip(null);
    };
    const down = () => { clear(); dismissed.current = false; ended.current = false; committed.current = null; };
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') clear(); };
    document.addEventListener('pointerdown', down, true);
    document.addEventListener('keydown', key, true);
    document.addEventListener('pointercancel', clear, true);
    window.addEventListener('blur', clear);
    return () => {
      clear();
      document.removeEventListener('pointerdown', down, true);
      document.removeEventListener('keydown', key, true);
      document.removeEventListener('pointercancel', clear, true);
      window.removeEventListener('blur', clear);
    };
  }, []);
  const feedbackFor = useCallback((from: Endpoint | null, to: Endpoint | null, finalVerdict?: ConnectionVerdict | null): ConnectionTooltip | null => {
    if (!from || !to) return null;
    const target = document.querySelector<HTMLElement>(`.react-flow__node[data-id="${CSS.escape(to.nodeId)}"] .react-flow__handle.${to.type}[data-handleid="${CSS.escape(to.id ?? '')}"]`);
    if (!target) return null;
    const source = from.type === 'source' ? from : to;
    const dest = from.type === 'source' ? to : from;
    const verdict = finalVerdict ?? (from.type === to.type ? { ok: false, reason: '请把输出端口连接到输入端口。' }
      : validate({ source: source.nodeId, sourceHandle: source.id ?? null, target: dest.nodeId, targetHandle: dest.id ?? null }));
    return { target, verdict };
  }, [validate]);
  useEffect(() => {
    // End feedback is owned by onConnectEnd, including very fast releases.
    // Unrelated graph refreshes must not restart its expiry timer.
    if (!connection.inProgress || ended.current || dismissed.current) return;
    clearTimeout(timer.current);
    const from = connection.fromNode && connection.fromType ? { nodeId: connection.fromNode, id: connection.fromId, type: connection.fromType } : null;
    const to = connection.toNode && connection.toType ? { nodeId: connection.toNode, id: connection.toId, type: connection.toType } : null;
    showConnectionTooltip(feedbackFor(from, to));
  }, [connection, feedbackFor]);
  const onConnectEnd = useCallback<OnConnectEnd>((_event, state) => {
    ended.current = true;
    clearTimeout(timer.current);
    if (dismissed.current) return;
    // onConnect may already have added the edge. Reuse its pre-mutation verdict
    // so a successful connection is not then reported as a duplicate.
    const tip = feedbackFor(state.fromHandle, state.toHandle, committed.current);
    showConnectionTooltip(tip && !tip.verdict.ok ? tip : null);
    if (tip && !tip.verdict.ok) timer.current = setTimeout(() => showConnectionTooltip(null), 4000);
  }, [feedbackFor]);
  const recordVerdict = useCallback((verdict: ConnectionVerdict) => { committed.current = verdict; }, []);
  return { onConnectEnd, recordVerdict };
}
