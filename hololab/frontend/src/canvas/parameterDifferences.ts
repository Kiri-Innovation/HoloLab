import type { CatalogPack } from "../wire";
import { packParameterRows, parameterEditorValue } from "./PackParameterField";

export type ParameterDifferenceReason = "workflow-variable" | "normalized-path" | "history-missing" | "resolved-value";
export const differenceReasons: Record<ParameterDifferenceReason, string> = {
  "workflow-variable": "工作流变量解析",
  "normalized-path": "环境相关路径规范化",
  "history-missing": "历史未记录",
  "resolved-value": "解析结果与字段显示值不同",
};

// JSON object key order is immaterial; array order, null and scalar types are not.
export function sameParameterValue(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true;
  if (!a || !b || typeof a !== "object" || typeof b !== "object") return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  const left = a as Record<string, unknown>, right = b as Record<string, unknown>;
  const keys = Object.keys(left);
  return keys.length === Object.keys(right).length && keys.every(key =>
    Object.prototype.hasOwnProperty.call(right, key) && sameParameterValue(left[key], right[key]));
}

export interface ParameterDifference {
  name: string;
  displayed: unknown;
  effective: unknown;
  reason: ParameterDifferenceReason;
}

/** Provenance is explicit: never infer variable bindings or historical values from strings. */
export function parameterDifferences(pack: CatalogPack, params: Record<string, unknown>,
  effective: Record<string, unknown> | undefined, environmentParams: string[] = [],
  provenance: Record<string, ParameterDifferenceReason> = {},
  bindings: Record<string, string> = {},
): ParameterDifference[] {
  if (!effective) return [];
  return packParameterRows(pack, params).flatMap(row => row.names.flatMap(name => {
    const reason = bindings[name] !== undefined ? "workflow-variable" : provenance[name];
    // Missing history is evidence, not an invitation to consult today's defaults.
    const displayed = bindings[name] !== undefined ? `→ ${bindings[name]}` : reason === "history-missing" ? undefined
      : parameterEditorValue(pack, params, name, row.names.length > 1);
    const actual = effective[name];
    if (sameParameterValue(displayed, actual)) return [];
    return [{ name, displayed, effective: actual, reason: reason ?? (
      pack.params[name].type === "path" && environmentParams.includes(name)
        ? "normalized-path" : "resolved-value") }];
  }));
}

export function parameterDifferenceText(diff: ParameterDifference): string {
  const show = (value: unknown) => value === undefined
    ? (diff.reason === "history-missing" ? "历史未记录" : "未提供")
    : typeof value === "string" ? (value || '""') : JSON.stringify(value);
  return `${diff.name}：显示 ${show(diff.displayed)} / 实际用 ${show(diff.effective)} / ${differenceReasons[diff.reason]}`;
}
