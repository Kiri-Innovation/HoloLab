import { Handle, Position, type NodeProps } from '@xyflow/react';
import type { AlgorithmNodeData } from './AlgorithmNode';
import { constantError } from './constants';
import { PinnedParameters } from './PinnedParameters';

export function ConstantNode({id, data}: NodeProps) {
  const d = data as AlgorithmNodeData;
  const error = constantError(d.pack.name, d.params ?? {});
  const vector = d.pack.name.endsWith('vec3-array');
  const count = ((d.params?.[vector ? 'x' : 'items'] ?? []) as unknown[]).length;
  return <div className="hl-algorithm-node" data-hl-constant="" style={{minWidth: 280, padding: 16, border: '1px solid var(--border)', borderRadius: 12, background: 'var(--surface-2)', color: 'var(--text-body)'}}>
    <strong>{vector ? 'vec3-array' : 'float-array'}</strong><div>constant · {vector ? 'vec3' : 'float'}[layer:{count}]</div>
    <div role="status">{error ? `值无效：${error}` : '已就绪'}</div>
    <PinnedParameters id={id} data={{...d, pinnedParams: d.pinnedParams ?? Object.keys(d.pack.params)}} />
    <Handle type="source" id={vector ? 'transforms' : 'offsets'} position={Position.Right} />
  </div>;
}
