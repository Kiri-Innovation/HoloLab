import type { Transform } from "./composeGeometry";

/** Authoring assistance only: never used by platform or algorithm validation. */
export function repairComposeValue(
  port: "transforms" | "offsets",
  value: unknown,
  count: number,
) {
  const changes: string[] = [];
  const show = (v: unknown): string =>
    typeof v === "number" && !Number.isFinite(v)
      ? String(v)
      : (JSON.stringify(v) ?? "未设置");
  const record = (path: string, before: unknown, after: unknown) =>
    changes.push(`${path} ${show(before)} → ${show(after)}`);
  const rows = Array.isArray(value) ? value : [];
  if (!Array.isArray(value)) record(port, value, []);
  if (rows.length > count) record(`${port}.length`, rows.length, count);
  const finite = (v: unknown): v is number =>
    typeof v === "number" && Number.isFinite(v);
  const result = Array.from({ length: count }, (_, i) => {
    const path = `${port}[${i}]`,
      old = rows[i];
    if (port === "offsets") {
      const next = finite(old) && old >= 0 ? old : 0;
      if (next !== old) record(path, old, next);
      return next;
    }
    if (!Array.isArray(old)) {
      record(path, old, [0, 0, 1]);
      return [0, 0, 1] as Transform;
    }
    if (old.length !== 3) record(`${path}.length`, old.length, 3);
    return [0, 1, 2].map((j) => {
      const next =
        finite(old[j]) && (j !== 2 || old[j] > 0) ? old[j] : j === 2 ? 1 : 0;
      if (next !== old[j])
        record(`${path}.${["x", "y", "scale"][j]}`, old[j], next);
      return next;
    }) as Transform;
  });
  return { value: result, changes };
}
