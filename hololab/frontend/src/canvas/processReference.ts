import { flopsInsertReference, flopsInsertReferenceAvailable } from "../flops";
import { copyReferenceText } from "./referenceAction";

export interface ProcessReference {
  metric?: "cpu" | "mem" | "gpu" | "vram"; usage_percent?: number | null; pid: number; name: string; used_mb: number | null; nodeId: string; sampledAt: number;
}
export function processReferenceText(p: ProcessReference, url: string): string {
  const metric = p.metric ?? "vram";
  const amount = metric === "cpu" || metric === "gpu" ? (p.usage_percent == null ? "Unknown" : `${p.usage_percent.toFixed(1)}%`) : p.used_mb == null ? "Unknown" : `${(p.used_mb / 1024).toFixed(1)} GiB`;
  return `${p.name} (PID ${p.pid}) · External / 非 HoloLab\nCompute node: ${p.nodeId}\n${metric.toUpperCase()}: ${amount}\nObserved: ${new Date(p.sampledAt * 1000).toISOString()}\n${url}`;
}
export async function copyProcessReference(p: ProcessReference) {
  const url = window.location.href;
  const text = processReferenceText(p, url);
  if (!flopsInsertReferenceAvailable()) return copyReferenceText(text);
  try {
    // This is a page link with an observation, not a resolvable process entity.
    const result = await flopsInsertReference({
      schemaVersion: 1, provider: "hololab", type: "link",
      resource: { uri: url },
      display: { title: `${p.name} (PID ${p.pid})`, subtitle: `External · ${p.nodeId}`, icon: "link" },
      access: { mode: "link_only" }, snapshot: { text, mediaType: "text/plain" },
    });
    if (result.success) return { message: "已插入 Flops 输入框" };
    return copyReferenceText(text, `插入失败：${result.reason || "接口未提供原因"}`);
  } catch {
    return copyReferenceText(text, "插入未确认，不自动重试");
  }
}
