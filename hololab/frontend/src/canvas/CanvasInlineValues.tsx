import { memo, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useStore, useUpdateNodeInternals } from '@xyflow/react';
import type { InputPortSpec } from '../wire';
import { inputValue, supportsInline } from './inlineValues';
import './canvasInlineValues.css';

export type InlineField = {
  name: string;
  spec: InputPortSpec;
  value: unknown;
  array: boolean;
};
const PORT_PAGE = 4;
const ROW_PAGE = 8;

/** Never stringify a whole array: formatting work is bounded by the visible cells. */
export function inlineCell(value: unknown): string {
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  if (Array.isArray(value)) {
    if (
      value.length === 3 &&
      value.every((v) => typeof v === 'number' && Number.isFinite(v))
    )
      return value.map(String).join(' · ');
    return `${value.length} items`;
  }
  return 'Invalid value';
}

function FieldPreview({ field }: { field: InlineField }) {
  const [expanded, setExpanded] = useState(false);
  const [page, setPage] = useState(0);
  const array = field.array && Array.isArray(field.value) ? field.value : null;
  const count = array?.length ?? 0;
  const size = expanded ? ROW_PAGE : 3;
  const current = Math.min(page, Math.max(0, Math.ceil(count / size) - 1));
  const start = expanded ? current * size : 0;
  return (
    <div className="hl-inline-field">
      <div className="hl-inline-field-heading">
        <span className="hl-inline-name" title={field.name}>
          {field.name}
        </span>
        <span className="hl-inline-type">
          {field.spec.tags[0]}
          {field.array ? ` [${count}]` : ''}
        </span>
      </div>
      {array ? (
        <>
          {count === 0 ? (
            <div className="hl-inline-empty">
              [] <span>Empty array</span>
            </div>
          ) : (
            <div className="hl-inline-rows">
              {array.slice(start, start + size).map((value, i) => (
                <div className="hl-inline-row" key={start + i}>
                  <span className="hl-inline-index">{start + i}</span>
                  <code title={inlineCell(value)}>{inlineCell(value)}</code>
                </div>
              ))}
            </div>
          )}
          {count > 3 && (
            <div className="hl-inline-navigation">
              <button
                type="button"
                aria-expanded={expanded}
                onClick={() => {
                  setExpanded(!expanded);
                  setPage(0);
                }}
              >
                {expanded ? 'Collapse' : `Show more · ${count - 3} remaining`}
              </button>
              {expanded && count > ROW_PAGE && (
                <>
                  <button
                    type="button"
                    aria-label={`Previous ${field.name} rows`}
                    disabled={current === 0}
                    onClick={() => setPage(current - 1)}
                  >
                    ‹
                  </button>
                  <span>
                    {start + 1}–{Math.min(start + size, count)}
                  </span>
                  <button
                    type="button"
                    aria-label={`Next ${field.name} rows`}
                    disabled={start + size >= count}
                    onClick={() => setPage(current + 1)}
                  >
                    ›
                  </button>
                </>
              )}
            </div>
          )}
        </>
      ) : (
        <code className="hl-inline-scalar" title={inlineCell(field.value)}>
          {inlineCell(field.value)}
        </code>
      )}
    </div>
  );
}

/** Stateless inputs; local state only controls presentation, never workflow values. */
export function InlineValuesPanel({
  fields,
  compact = false,
}: {
  fields: InlineField[];
  compact?: boolean;
}) {
  const [page, setPage] = useState(0);
  if (!fields.length) return null;
  const current = Math.min(page, Math.ceil(fields.length / PORT_PAGE) - 1);
  return (
    <section
      className="hl-canvas-inline nodrag nopan nowheel"
      aria-label="Inline input values"
      data-compact={compact || undefined}
      onKeyDown={(e) => e.stopPropagation()}
      onPointerDown={(e) => e.stopPropagation()}
      onDoubleClick={(e) => e.stopPropagation()}
    >
      <div className="hl-inline-heading">
        <span>Input values</span>
        <span title="Read only. Select the node to edit its inputs in Inspector.">
          {compact ? `${fields.length} fields · zoom in` : 'read only'}
        </span>
      </div>
      {!compact && (
        <>
          {fields
            .slice(current * PORT_PAGE, (current + 1) * PORT_PAGE)
            .map((field) => (
              <FieldPreview key={field.name} field={field} />
            ))}
          {fields.length > PORT_PAGE && (
            <div className="hl-inline-navigation">
              <button
                type="button"
                aria-label="Previous input fields"
                disabled={current === 0}
                onClick={() => setPage(current - 1)}
              >
                ‹
              </button>
              <span>
                {current * PORT_PAGE + 1}–
                {Math.min((current + 1) * PORT_PAGE, fields.length)} /{' '}
                {fields.length} inputs
              </span>
              <button
                type="button"
                aria-label="Next input fields"
                disabled={(current + 1) * PORT_PAGE >= fields.length}
                onClick={() => setPage(current + 1)}
              >
                ›
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}

export const CanvasInlineValues = memo(function CanvasInlineValues({
  id,
  inputs,
  values,
  connected,
  fanout = false,
  frozen = false,
}: {
  id: string;
  inputs: Record<string, InputPortSpec>;
  values?: Record<string, unknown>;
  connected: string;
  fanout?: boolean;
  frozen?: boolean;
}) {
  // Boolean selector: panning/zooming doesn't render every value on every frame.
  const compact = useStore((s) => s.transform[2] < 0.6);
  const fields = useMemo(() => {
    const wired: string[] = JSON.parse(connected);
    return Object.entries(inputs).flatMap(([name, spec]) => {
      const value = frozen
        ? values?.[name]
        : inputValue(spec, values ?? {}, name);
      return wired.includes(name) || !supportsInline(spec) || value == null
        ? []
        : [
            {
              name,
              spec,
              value,
              array: !!(
                spec.arrayed ||
                spec.dim_labels?.length ||
                (fanout && !spec.scalar)
              ),
            },
          ];
    });
  }, [inputs, values, connected, fanout, frozen]);
  const root = useRef<HTMLDivElement>(null);
  const update = useUpdateNodeInternals();
  useLayoutEffect(() => {
    let frame = 0;
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => update(id));
    });
    if (root.current) observer.observe(root.current);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(frame);
    };
  }, [id, update]);
  return (
    <div
      ref={root}
      className="hl-canvas-inline-wrap"
      data-values={fields.length ? '' : undefined}
      style={{ display: fields.length ? 'block' : 'none' }}
    >
      <InlineValuesPanel fields={fields} compact={compact} />
    </div>
  );
});
