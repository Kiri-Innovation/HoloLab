import type { Connection } from '@xyflow/react';
import { portsCompatible, effectivePortArrayed, effectivePortDimLabels, tagsCompatible } from '../tags';
import { effectiveOutputType, formatTypeLabelLong, type EdgeTypeCtx } from './edgeLabels';

export interface ConnectionVerdict {
  ok: boolean;
  reason?: string;
  expected?: string;
  actual?: string;
}

/** The same local decision is used during dragging and before adding an edge. */
export function validateConnection(conn: Connection, ctx: EdgeTypeCtx,
  viewIds: ReadonlySet<string> = new Set()): ConnectionVerdict {
  const reject = (reason: string): ConnectionVerdict => ({ ok: false, reason });
  if (!conn.sourceHandle || !conn.targetHandle) return reject('找不到连接端口。');
  if (viewIds.has(conn.source)) return reject('预览卡片不能作为数据输出。');
  if (viewIds.has(conn.target)) {
    if (conn.targetHandle !== 'in') return reject('请选择预览卡片的输入端口。');
    if (ctx.edges.some(e => e.target === conn.target && e.targetHandle === 'in')) return reject('预览卡片已有输入，请先移除原连线。');
    return { ok: true };
  }
  const src = ctx.nodes.find(n => n.id === conn.source);
  const tgt = ctx.nodes.find(n => n.id === conn.target);
  if (!src || !tgt) return reject('节点信息尚未加载。');
  const srcPack = ctx.catalogByKey.get(`${src.algorithm_name}@${src.algorithm_version}`);
  const pack = ctx.catalogByKey.get(`${tgt.algorithm_name}@${tgt.algorithm_version}`);
  const input = pack?.inputs[conn.targetHandle];
  if (!srcPack?.outputs[conn.sourceHandle] || !pack || !input) return reject('端口声明不可用，请检查算法版本。');
  // addEdge also suppresses duplicates; do not promise a new edge in that case.
  if (ctx.edges.some(e => e.source === conn.source && e.sourceHandle === conn.sourceHandle
    && e.target === conn.target && e.targetHandle === conn.targetHandle)) return reject('这两个端口已经连接。');
  const source = effectiveOutputType(conn.source, conn.sourceHandle, ctx);
  const target = {
    tags: input.tags,
    arrayed: effectivePortArrayed(input.arrayed, pack.arrayable, tgt.arrayed_toggle, input.scalar),
    dimLabels: effectivePortDimLabels(input.arrayed, input.dim_labels, pack.arrayable, tgt.arrayed_toggle, input.scalar),
  };
  const ok = portsCompatible(source.tags, source.arrayed, target.tags, target.arrayed,
    input.scalar, source.dimLabels, target.dimLabels, input.require_axis_label_match ?? true);
  const reason = ok ? undefined : !tagsCompatible(source.tags, target.tags) ? '数据类型不匹配。'
    : source.arrayed !== target.arrayed ? '单个数据与数组不能直接连接。'
    : !portsCompatible(source.tags, source.arrayed, target.tags, target.arrayed,
      input.scalar, source.dimLabels, target.dimLabels, false) ? '数组维度层数不匹配。'
    : '数组轴名称不匹配。';
  return { ok, reason, expected: formatTypeLabelLong(target), actual: formatTypeLabelLong(source) };
}
