import { NodeCardHeader } from "./NodeCardHeader";
import { Handle, Position, type NodeProps } from '@xyflow/react';
import type { AlgorithmNodeData } from './AlgorithmNode';
import { constantError } from './constants';
import { PinnedParameters } from './PinnedParameters';

export function ConstantNode({id, data}: NodeProps) {
  const d = data as AlgorithmNodeData;
  const error = constantError(d.pack.name, d.params ?? {});
  const vector = d.pack.name.endsWith('vec3-array');
  const count = ((d.params?.[vector ? 'x' : 'items'] ?? []) as unknown[]).length;
  return <div className="hl-algorithm-node" data-hl-constant="" style={{minWidth: 280,  border: '1px solid var(--border)', borderRadius: 'var(--radius-md)', background: 'var(--surface-2)', color: 'var(--text-body)'}}>
    <NodeCardHeader id={id} name={vector ? 'vec3-array' : 'float-array'} version={d.pack.version} subtitle={`constant · ${vector ? 'vec3' : 'float'}[layer:${count}]`}
      status={{ tone: error ? 'error' : 'success', conclusion: error ? '值无效' : '已就绪', reason: error || '常量值符合格式，无需运行。', action: d.readOnly ? '这是快照中的冻结值。' : '可直接编辑常量值。' }} />
    <div style={{padding: 16}}>
    <div role="status">{error ? `值无效：${error}` : '已就绪'}</div>
    <PinnedParameters id={id} data={{...d, pinnedParams: d.pinnedParams ?? Object.keys(d.pack.params)}} />
    </div>
    <Handle type="source" id={vector ? 'transforms' : 'offsets'} position={Position.Right} />
  </div>;
}
