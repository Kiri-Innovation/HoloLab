// DimListEditor — an ordered list of short string labels edited in place.
//
// Used by NodeInspector for ``list[str]`` params such as ``regroup``'s
// ``input_dims`` / ``output_dims``. Rules:
//   * Reorder via ↑/↓ buttons on each row (small, one-hand friendly).
//   * Rename inline. Trailing whitespace stripped on blur.
//   * Add / remove rows.
//   * When ``matchAgainst`` is passed, the editor flags the current
//     value as "not a permutation of X" — used to keep
//     ``output_dims`` a permutation of ``input_dims`` in the regroup
//     pack. Passive hint, not a hard block: the operator can commit
//     anyway if they know what they're doing (backend still validates).
//
// Style: same tokens the surrounding NodeInspector already uses
// (LABEL, HINT, TAG). No fresh colour palette introduced.

import { useCallback, useMemo, type CSSProperties } from "react";

export interface DimListEditorProps {
  value: string[];
  onChange: (next: string[]) => void;
  /** Sister list that ``value`` must be a permutation of. When set and
   *  the two aren't a permutation match, we render a validation hint.
   *  Same-element repeats count once — permutation strictness is on. */
  matchAgainst?: string[];
  /** ``ok / warn`` on rows that would fail the sister-permutation check
   *  ("this label isn't in the input dims" or "duplicate"). */
  placeholder?: string;
  /** Passed straight to each row's <input> as a name for accessibility
   *  hooks. Suffixed with ``-<i>`` so screen readers can announce each
   *  row uniquely. */
  ariaName?: string;
}

const ROW: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
  padding: "3px 0",
};

const CELL_INPUT: CSSProperties = {
  flex: 1,
  fontFamily: "var(--font-mono)",
  fontSize: "var(--fs-xs)",
  padding: "3px 6px",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-sm)",
  background: "var(--surface)",
  color: "var(--text-body)",
};

const CELL_INPUT_WARN: CSSProperties = {
  ...CELL_INPUT,
  borderColor: "var(--status-failed)",
};

const ICON_BTN: CSSProperties = {
  appearance: "none",
  border: "1px solid var(--border)",
  background: "var(--surface)",
  color: "var(--text-muted)",
  width: 22,
  height: 22,
  padding: 0,
  borderRadius: "var(--radius-sm)",
  fontSize: 11,
  fontWeight: 600,
  cursor: "pointer",
  lineHeight: 1,
  fontFamily: "var(--font-mono)",
};

const ICON_BTN_DISABLED: CSSProperties = {
  ...ICON_BTN,
  opacity: 0.35,
  cursor: "default",
};

/** Permutation check — same multiset. Empty strings count as label
 *  values so ``["","",""]`` vs ``["","",""]`` matches. */
function isPermutationOf(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  const counter = new Map<string, number>();
  for (const x of a) counter.set(x, (counter.get(x) ?? 0) + 1);
  for (const x of b) {
    const n = counter.get(x);
    if (!n) return false;
    counter.set(x, n - 1);
  }
  return true;
}

export function DimListEditor({
  value,
  onChange,
  matchAgainst,
  placeholder,
  ariaName,
}: DimListEditorProps) {
  const isPermutation = useMemo(() => {
    if (!matchAgainst) return true;
    return isPermutationOf(value, matchAgainst);
  }, [value, matchAgainst]);

  const setAt = useCallback(
    (i: number, next: string) => {
      const copy = value.slice();
      copy[i] = next;
      onChange(copy);
    },
    [onChange, value],
  );

  const moveUp = useCallback(
    (i: number) => {
      if (i === 0) return;
      const copy = value.slice();
      [copy[i - 1], copy[i]] = [copy[i], copy[i - 1]];
      onChange(copy);
    },
    [onChange, value],
  );

  const moveDown = useCallback(
    (i: number) => {
      if (i === value.length - 1) return;
      const copy = value.slice();
      [copy[i], copy[i + 1]] = [copy[i + 1], copy[i]];
      onChange(copy);
    },
    [onChange, value],
  );

  const removeAt = useCallback(
    (i: number) => {
      const copy = value.slice();
      copy.splice(i, 1);
      onChange(copy);
    },
    [onChange, value],
  );

  const append = useCallback(() => {
    onChange([...value, ""]);
  }, [onChange, value]);

  // Which rows carry a warning: duplicates or "not in matchAgainst".
  const rowWarn = useMemo(() => {
    const flags = value.map(() => false);
    if (matchAgainst) {
      const allowed = new Map<string, number>();
      for (const x of matchAgainst) allowed.set(x, (allowed.get(x) ?? 0) + 1);
      const used = new Map<string, number>();
      for (let i = 0; i < value.length; i++) {
        const v = value[i];
        const cap = allowed.get(v) ?? 0;
        const seen = used.get(v) ?? 0;
        if (cap === 0 || seen >= cap) flags[i] = true;
        used.set(v, seen + 1);
      }
    }
    return flags;
  }, [value, matchAgainst]);

  return (
    <div data-hl-dim-list-editor="" data-hl-dim-list-length={value.length}>
      {value.map((v, i) => (
        <div key={i} style={ROW}>
          <button
            type="button"
            title="move up"
            onClick={() => moveUp(i)}
            disabled={i === 0}
            aria-label={`move ${v || "row"} up`}
            style={i === 0 ? ICON_BTN_DISABLED : ICON_BTN}
          >
            ↑
          </button>
          <button
            type="button"
            title="move down"
            onClick={() => moveDown(i)}
            disabled={i === value.length - 1}
            aria-label={`move ${v || "row"} down`}
            style={i === value.length - 1 ? ICON_BTN_DISABLED : ICON_BTN}
          >
            ↓
          </button>
          <input
            value={v}
            onChange={(e) => setAt(i, e.target.value)}
            onBlur={(e) => {
              const trimmed = e.target.value.trim();
              if (trimmed !== e.target.value) setAt(i, trimmed);
            }}
            aria-label={ariaName ? `${ariaName}-${i}` : undefined}
            placeholder={placeholder ?? "label"}
            style={rowWarn[i] ? CELL_INPUT_WARN : CELL_INPUT}
          />
          <button
            type="button"
            title="remove"
            onClick={() => removeAt(i)}
            aria-label={`remove ${v || "row"}`}
            style={ICON_BTN}
          >
            ×
          </button>
        </div>
      ))}
      <div style={{ display: "flex", gap: 6, marginTop: 4 }}>
        <button
          type="button"
          onClick={append}
          style={{ ...ICON_BTN, width: "auto", padding: "0 10px" }}
        >
          + add
        </button>
      </div>
      {matchAgainst && !isPermutation && (
        <div
          data-hl-dim-list-warn=""
          style={{
            marginTop: 6,
            fontSize: "var(--fs-xs)",
            color: "var(--status-failed)",
            lineHeight: 1.4,
          }}
        >
          must be a permutation of {matchAgainst.length === 0 ? "(empty)" : matchAgainst.join(", ")}
        </div>
      )}
    </div>
  );
}
