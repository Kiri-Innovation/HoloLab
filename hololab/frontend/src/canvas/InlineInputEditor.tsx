import { useEffect, useState } from 'react';
import type { CatalogPack, GraphNode, ParamSpec } from '../wire';
import {
  ArrayParameterInput,
  ParameterInput,
  Vec3ArrayParameterInput,
} from './ParameterInput';
import { inlineError, inputValue, supportsInline } from './inlineValues';
const numberSpec: ParamSpec = {
  type: 'float',
  default: null,
  description: null,
  optional: false,
};
const arraySpec: ParamSpec = {
  ...numberSpec,
  type: 'array',
  items: 'float',
  default: [],
};
function NestedLiteralEditor({
  name,
  value,
  onChange,
}: {
  name: string;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const encoded = JSON.stringify(value);
  const [draft, setDraft] = useState(encoded);
  const [error, setError] = useState(false);
  useEffect(() => {
    setDraft(encoded);
    setError(false);
  }, [encoded]);
  return (
    <>
      <textarea
        aria-label={`${name} JSON array`}
        value={draft}
        onChange={(e) => {
          setDraft(e.target.value);
          try {
            onChange(JSON.parse(e.target.value));
            setError(false);
          } catch {
            setError(true);
          }
        }}
      />
      {error && <small role="alert">JSON 尚未完整；未保存此编辑。</small>}
    </>
  );
}
export function InlineInputEditor({
  node,
  pack,
  sources = {},
  onChange,
}: {
  node: GraphNode;
  pack: CatalogPack;
  sources?: Record<string, string>;
  onChange: (values: Record<string, unknown>) => void;
}) {
  return (
    <section aria-label="Input values">
      <h3>输入值</h3>
      {Object.entries(pack.inputs).map(([name, spec]) => {
        const source = sources[name];
        const values = node.port_values ?? {};
        const value = inputValue(spec, values, name);
        const set = (v: unknown) => onChange({ ...values, [name]: v });
        const fanout = !!(node.arrayed_toggle && pack.arrayable);
        const error = inlineError(spec, value, fanout);
        const declaredArray = !!(spec.arrayed || spec.dim_labels?.length);
        const array = declaredArray || (fanout && !spec.scalar);
        const vector = spec.tags[0] === 'vec3';
        const editable = supportsInline(spec);
        const context = { nodeId: node.assigned_node_id, initialDirectory: '' };
        let editor = null;
        if (editable && value != null) {
          if (fanout && !spec.scalar && declaredArray)
            editor = (
              <NestedLiteralEditor name={name} value={value} onChange={set} />
            );
          else if (
            vector &&
            (array
              ? Array.isArray(value) &&
                value.every((v) => Array.isArray(v) && v.length === 3)
              : Array.isArray(value) && value.length === 3)
          ) {
            const rows = (array ? value : [value]) as number[][];
            const params = Object.fromEntries(
              ['x', 'y', 'scale'].map((k, c) => [k, rows.map((v) => v[c])]),
            );
            editor = array ? (
              <Vec3ArrayParameterInput
                {...context}
                specs={{ x: arraySpec, y: arraySpec, scale: arraySpec }}
                params={params}
                onChange={(p) =>
                  set(
                    (p.x as unknown[]).map((_, i) => [
                      (p.x as unknown[])[i],
                      (p.y as unknown[])[i],
                      (p.scale as unknown[])[i],
                    ]),
                  )
                }
              />
            ) : (
              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(3,minmax(0,1fr))',
                  gap: 5,
                }}
              >
                {['x', 'y', 'scale'].map((k, c) => (
                  <label key={k}>
                    {k}
                    <ParameterInput
                      {...context}
                      name={`${name}.${k}`}
                      spec={numberSpec}
                      value={(value as number[])[c]}
                      onChange={(v) =>
                        set(
                          (value as unknown[]).map((old, i) =>
                            i === c ? v : old,
                          ),
                        )
                      }
                    />
                  </label>
                ))}
              </div>
            );
          } else if (!vector) {
            const Component = array ? ArrayParameterInput : ParameterInput;
            editor = (
              <Component
                {...context}
                name={name}
                spec={array ? arraySpec : numberSpec}
                value={value}
                onChange={set}
              />
            );
          } else
            editor = (
              <button type="button" onClick={() => set(array ? [] : [0, 0, 0])}>
                重置格式
              </button>
            );
        }
        return (
          <div key={name} style={{ marginBottom: 14, minWidth: 0 }}>
            <strong>{name}</strong>{' '}
            <small>
              {source
                ? `← ${source}${editable ? "（内联备用值不生效）" : ""}`
                : values[name] != null
                  ? '内联值'
                  : spec.inline_default != null
                    ? '端口默认值'
                    : '未设置'}
            </small>
            {source && editable && <div>连线优先；断线后恢复内联值。</div>}
            {editable && (
              <>
                {value == null ? (
                  <button
                    type="button"
                    onClick={() => set(array ? [] : vector ? [0, 0, 0] : 0)}
                  >
                    设置内联值
                  </button>
                ) : (
                  editor
                )}
                {values[name] != null && (
                  <button type="button" onClick={() => set(null)}>
                    清除内联值
                  </button>
                )}
              </>
            )}
            {!editable && !source && (
              <small>请连接上游{spec.required ? '（必填）' : ''}</small>
            )}
            {error && (
              <div role="alert" style={{ color: 'var(--status-failed)' }}>
                {source ? '备用值：' : ''}
                {error}
              </div>
            )}
          </div>
        );
      })}
    </section>
  );
}
