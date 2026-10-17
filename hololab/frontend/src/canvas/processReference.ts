import { insertOrCopyReference } from "./referenceAction";

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
  // This is a page link with an observation, not a resolvable process entity.
  return insertOrCopyReference({
    schemaVersion: 1, provider: "hololab", type: "link",
    resource: { uri: url },
    display: { title: `${p.name} (PID ${p.pid})`, subtitle: `External · ${p.nodeId}`, icon: "link" },
    access: { mode: "link_only" }, snapshot: { text, mediaType: "text/plain" },
  }, text);
}
