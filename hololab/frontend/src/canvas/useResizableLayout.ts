import { useCallback, useEffect, useState } from "react";

export interface ResizableSlot {
  value: number;
  resize: (delta: number) => void;
  reset: () => void;
}

function readStoredSize(key: string, fallback: number, min: number, max: number): number {
  try {
    const stored = Number(window.localStorage.getItem(key));
    return Number.isFinite(stored) ? Math.min(max, Math.max(min, stored)) : fallback;
  } catch {
    return fallback;
  }
}

/** A clamped layout measurement that survives a browser restart. */
export function useResizableSlot(
  key: string,
  fallback: number,
  min: number,
  max: number,
): ResizableSlot {
  const [value, setValue] = useState(() => readStoredSize(key, fallback, min, max));

  useEffect(() => {
    try {
      window.localStorage.setItem(key, String(value));
    } catch {
      // Storage can be disabled; resizing should remain useful for this visit.
    }
  }, [key, value]);

  const resize = useCallback(
    (delta: number) => setValue((current) => Math.min(max, Math.max(min, current + delta))),
    [max, min],
  );

  const reset = useCallback(() => setValue(fallback), [fallback]);

  return { value, resize, reset };
}
