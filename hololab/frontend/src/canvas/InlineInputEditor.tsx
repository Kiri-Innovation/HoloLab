import { useEffect, useState, type ReactNode } from 'react';
import type { CatalogPack, GraphNode, InputPortSpec, ParamSpec } from '../wire';
import {
  ArrayParameterInput,
  ParameterInput,
  Vec3ArrayParameterInput,
} from './ParameterInput';
import { inlineError, inputValue, supportsInline, freshInlineValue } from './inlineValues';

export type InlineInputSource = 'inline' | 'port-default' | 'type-default' | 'unset';

export function inlineInputSource(
  values: Record<string, unknown>,
  name: string,
  spec: InputPortSpec,
  editable: boolean,
): InlineInputSource {
  if (values[name] != null) return 'inline';
  if (spec.inline_default != null) return 'port-default';
  return editable ? 'type-default' : 'unset';
}

export function toggledInlineInputEditor(expanded: boolean): boolean {
  return !expanded;
}

const INPUT_SOURCE_TEXT: Record<InlineInputSource, string> = {
  inline: '内联值',
  'port-default': '端口默认值',
  'type-default': '类型默认值',
  unset: '未设置',
};

function InputSourceIcon({ source }: { source: InlineInputSource }) {
  if (source === 'inline') return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="M5 4v16M12 4v16M19 4v16" /><circle cx="5" cy="9" r="2.25" fill="currentColor" /><circle cx="12" cy="15" r="2.25" fill="currentColor" /><circle cx="19" cy="7" r="2.25" fill="currentColor" /></svg>;
  if (source === 'port-default') return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="M8 5h10a1 1 0 0 1 1 1v11" /><path d="M5 8h10a1 1 0 0 1 1 1v10H6a1 1 0 0 1-1-1V8Z" /><path d="M8 12h5M8 15h4" /></svg>;
  if (source === 'type-default') return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><circle cx="12" cy="12" r="7" /><path d="M9 12h6M12 9v6" /></svg>;
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><circle cx="12" cy="12" r="7" /><path d="m7 17 10-10" /></svg>;
}

function InlineInputField({
  name, source, editable, array, vector, editor, hint, error,
}: {
  name: string;
  source: InlineInputSource;
  editable: boolean;
  array: boolean;
  vector: boolean;
  editor: ReactNode;
  hint: ReactNode;
  error: ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);
  const sourceText = INPUT_SOURCE_TEXT[source];
  const action = expanded ? '收起编辑控件' : '展开编辑控件';
  const controlsId = `inline-input-${name.replace(/[^a-zA-Z0-9_-]/g, '-')}`;
  const description = `${name}：${sourceText}${editable ? `；点击${action}` : '；请连接上游'}`;
  return (
    <div data-inline-port={name} className="hl-inspector-field hl-inspector-input-field">
      <div className="hl-inspector-input-label">
        <button
          type="button"
          className="hl-inspector-input-toggle"
          data-source={source}
          title={description}
          aria-label={description}
          aria-expanded={expanded}
          aria-controls={controlsId}
          disabled={!editable}
          onClick={() => setExpanded(toggledInlineInputEditor)}
        >
          <InputSourceIcon source={source} />
        </button>
        <code>{name}</code>
      </div>
      {editable && expanded && (
        <div id={controlsId} className={`hl-inspector-input-controls${array || vector ? ' hl-inspector-input-controls--wide' : ''}`}>
          {editor}
        </div>
      )}
      {!editable && <div className="hl-inspector-input-hint">{hint}</div>}
      {error}
    </div>
  );
}
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
export function unconnectedInputs(pack: CatalogPack, sources: Record<string, string> = {}) {
  return Object.entries(pack.inputs).filter(([name]) => !Object.prototype.hasOwnProperty.call(sources, name));
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
    <>
      {unconnectedInputs(pack, sources).map(([name, spec]) => {
        const values = node.port_values ?? {};
        const value = inputValue(spec, values, name) ?? freshInlineValue(spec, !!(node.arrayed_toggle && pack.arrayable));
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
        return <InlineInputField
          key={name}
          name={name}
          source={inlineInputSource(values, name, spec, editable)}
          editable={editable}
          array={array}
          vector={vector}
          editor={editor}
          hint={<>请连接上游{spec.required ? '（必填）' : ''}</>}
          error={error && <div className="hl-inspector-input-hint" role="alert" style={{ color: 'var(--status-failed)' }}>{error}</div>}
        />;
      })}
    </>
  );
}
