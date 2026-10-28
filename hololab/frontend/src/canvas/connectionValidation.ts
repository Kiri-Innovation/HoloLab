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
  // addEdge also suppresses duplicates; do not promise a new edge in that case.
  if (ctx.edges.some(e => e.source === conn.source && e.sourceHandle === conn.sourceHandle
    && e.target === conn.target && e.targetHandle === conn.targetHandle)) return reject('这两个端口已经连接。');
  if (viewIds.has(conn.target)) {
    if (conn.targetHandle !== 'in') return reject('请选择预览卡片的输入端口。');
    return { ok: true };
  }
  const src = ctx.nodes.find(n => n.id === conn.source);
  const tgt = ctx.nodes.find(n => n.id === conn.target);
  if (!src || !tgt) return reject('节点信息尚未加载。');
  if (tgt.kind === 'intrinsic' && tgt.assembly) {
    const a = tgt.assembly;
    if (!a.slots.includes(conn.targetHandle)) return reject('组装输入槽不存在。');
    const source = effectiveOutputType(conn.source, conn.sourceHandle, ctx);
    if (!source.tags.length || source.tags.includes('any')) return reject('组装需要已确定的具体类型。');
    if (source.arrayed && source.dimLabels.length >= 2) return reject('组装结果不能超过两层数组。');
    const inputs = ctx.edges.filter(e => e.target === conn.target);
    const anchor = inputs.find(e => e.targetHandle === a.type_anchor_slot) ?? a.slots.map(s => inputs.find(e => e.targetHandle === s)).find(Boolean);
    if (anchor) {
      const expected = effectiveOutputType(anchor.source, anchor.sourceHandle, ctx);
      const key = (t: typeof source) => JSON.stringify([Array.from(new Set(t.tags)).sort(),t.arrayed,t.dimLabels]);
      if (key(source) !== key(expected)) return reject(`组装要求严格同型：需要 ${formatTypeLabelLong(expected)}，收到 ${formatTypeLabelLong(source)}。`);
    }
    return {ok:true};
  }
  const srcPack = ctx.catalogByKey.get(`${src.algorithm_name}@${src.algorithm_version}`);
  const pack = ctx.catalogByKey.get(`${tgt.algorithm_name}@${tgt.algorithm_version}`);
  const input = pack?.inputs[conn.targetHandle];
  if (!srcPack?.outputs[conn.sourceHandle] || !pack || !input) return reject('端口声明不可用，请检查算法版本。');
  if (tgt.arrayed_toggle && input.any_of?.length) {
    return reject('联合类型输入不能启用 arrayed fan-out。');
  }
  const source = effectiveOutputType(conn.source, conn.sourceHandle, ctx);
  const variants = input.any_of?.length ? input.any_of : [input];
  const targets = variants.map(variant => ({
    tags: variant.tags,
    arrayed: effectivePortArrayed(variant.arrayed, pack.arrayable, tgt.arrayed_toggle, input.scalar),
    dimLabels: effectivePortDimLabels(variant.arrayed, variant.dim_labels, pack.arrayable, tgt.arrayed_toggle, input.scalar),
    strict: input.require_axis_label_match ?? true,
  }));
  const ok = targets.some(target => portsCompatible(source.tags, source.arrayed, target.tags, target.arrayed,
    input.scalar, source.dimLabels, target.dimLabels, target.strict));
  const tagMatch = targets.some(target => tagsCompatible(source.tags, target.tags));
  const cardinalityMatch = targets.some(target => source.arrayed === target.arrayed);
  const depthMatch = targets.some(target => portsCompatible(source.tags, source.arrayed, target.tags, target.arrayed,
    input.scalar, source.dimLabels, target.dimLabels, false));
  const reason = ok ? undefined : !tagMatch ? '数据类型不匹配。'
    : !cardinalityMatch ? '单个数据与数组不能直接连接。'
    : !depthMatch ? '数组维度层数不匹配。' : '数组轴名称不匹配。';
  return { ok, reason, expected: targets.map(formatTypeLabelLong).join(' | '), actual: formatTypeLabelLong(source) };
}
