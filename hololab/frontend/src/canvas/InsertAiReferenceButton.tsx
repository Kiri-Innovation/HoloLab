import { useEffect, useState } from "react";
import type { PreviewTarget, NodeRuntime } from "./AlgorithmNode";
import {
  flopsInsertReference,
  flopsInsertReferenceAvailable,
  type FlopsInsertReferenceParams,
} from "../flops";

interface InsertAiReferenceButtonProps {
  workflowId: string;
  workflowName: string;
  nodeId: string;
  algorithmName: string;
  algorithmVersion: string;
  params: Record<string, unknown>;
  runtime?: NodeRuntime;
  artifactTargets?: Record<string, PreviewTarget>;
}

type Status =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ok"; msg: string }
  | { kind: "err"; msg: string };

function ReferenceGlyph({ colour }: { colour: string }) {
  return (
    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke={colour} strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M10 13a5 5 0 0 0 7.07.07l2-2a5 5 0 0 0-7.07-7.07l-1.15 1.15" />
      <path d="M14 11a5 5 0 0 0-7.07-.07l-2 2A5 5 0 0 0 12 20l1.15-1.15" />
    </svg>
  );
}

function StatusGlyph({ kind, colour }: { kind: "ok" | "err"; colour: string }) {
  return kind === "ok" ? (
    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke={colour} strokeWidth="2.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m5 12 4 4L19 6" /></svg>
  ) : (
    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke={colour} strokeWidth="2.5" strokeLinecap="round" aria-hidden="true"><path d="M12 8v5" /><path d="M12 17h.01" /><circle cx="12" cy="12" r="9" /></svg>
  );
}

function truncateSnapshot(text: string): string {
  const limit = 60 * 1024;
  const bytes = new TextEncoder().encode(text);
  if (bytes.length <= limit) return text;
  return `${new TextDecoder().decode(bytes.slice(0, limit - 32))}\n\n_摘要已截断。_`;
}

function snapshotText(props: InsertAiReferenceButtonProps): string {
  const artifacts = Object.entries(props.artifactTargets ?? {}).map(([port, target]) => ({
    port,
    handle: target.handle_id,
    storage: target.storage,
    tags: target.tags,
    deleted: target.deleted,
  }));
  return truncateSnapshot([
    `# HoloLab workflow node`,
    "",
    `- Node: \`${props.nodeId}\``,
    `- Algorithm: \`${props.algorithmName}@${props.algorithmVersion}\``,
    `- Workflow: ${props.workflowName}`,
    `- Latest run status: ${props.runtime?.state ?? "not run"}`,
    props.runtime?.job_id ? `- Latest job: \`${props.runtime.job_id}\`` : "",
    props.runtime?.fail_reason ? `- Failure reason: ${props.runtime.fail_reason}` : "",
    "",
    "## Parameters",
    "```json",
    JSON.stringify(props.params, null, 2),
    "```",
    "",
    "## Output artifacts",
    "```json",
    JSON.stringify(artifacts, null, 2),
    "```",
  ].filter(Boolean).join("\n"));
}

function readableReason(reason: string | undefined): string {
  const labels: Record<string, string> = {
    invalid_params: "引用参数无效",
    unsupported_version: "Flops API 版本不受支持",
    unavailable: "Flops 暂不可用",
  };
  return labels[reason ?? ""] ?? reason ?? "插入引用失败";
}

export function InsertAiReferenceButton(props: InsertAiReferenceButtonProps) {
  const [available, setAvailable] = useState(() => flopsInsertReferenceAvailable());
  const [status, setStatus] = useState<Status>({ kind: "idle" });

  useEffect(() => {
    if (!available && flopsInsertReferenceAvailable()) setAvailable(true);
  }, [available]);
  useEffect(() => {
    if (status.kind === "ok" || status.kind === "err") {
      const timer = window.setTimeout(() => setStatus({ kind: "idle" }), 3000);
      return () => window.clearTimeout(timer);
    }
  }, [status.kind]);

  if (!available) return null;

  const onClick = async (event: React.MouseEvent) => {
    event.stopPropagation();
    setStatus({ kind: "loading" });
    const requestId = crypto.randomUUID();
    const payload: FlopsInsertReferenceParams = {
      schemaVersion: 1,
      provider: "hololab",
      type: "workflow_node",
      requestId,
      resource: { uri: `https://hololab.xenotech.studio/workflows/${encodeURIComponent(props.workflowId)}/nodes/${encodeURIComponent(props.nodeId)}` },
      display: {
        title: `${props.algorithmName}@${props.algorithmVersion}`,
        subtitle: `${props.nodeId} · ${props.workflowName}`,
        icon: "workflow",
      },
      access: { mode: "link_only" },
      snapshot: { text: snapshotText(props), mediaType: "text/markdown" },
    };
    try {
      const result = await flopsInsertReference(payload);
      if (result.success) {
        setStatus({ kind: "ok", msg: result.outcome === "already_present" ? "引用已在草稿中" : "已引用给 AI" });
      } else {
        setStatus({ kind: "err", msg: readableReason(result.reason) });
      }
    } catch (error) {
      setStatus({ kind: "err", msg: error instanceof Error ? error.message : "调用失败" });
    }
  };

  const colour = status.kind === "ok" ? "var(--success)" : status.kind === "err" ? "var(--error)" : "var(--text-muted)";
  const title = status.kind === "loading" ? "正在引用给 AI…" : status.kind === "ok" || status.kind === "err" ? status.msg : "引用给 AI";

  return (
    <span style={{ position: "relative", display: "inline-flex" }}>
      <button type="button" onClick={onClick} disabled={status.kind === "loading"} title={title} aria-label="引用给 AI" data-hl-insert-reference="" style={{ border: "1px solid var(--border-strong)", background: "transparent", color: colour, width: 16, height: 16, borderRadius: "var(--radius-sm)", cursor: status.kind === "loading" ? "wait" : "pointer", display: "flex", alignItems: "center", justifyContent: "center", padding: 0, flexShrink: 0, lineHeight: 1, transition: "color var(--dur-fast) var(--ease), border-color var(--dur-fast) var(--ease)" }}>
        {status.kind === "loading" ? <span style={{ fontSize: 9 }}>…</span> : status.kind === "ok" || status.kind === "err" ? <StatusGlyph kind={status.kind} colour={colour} /> : <ReferenceGlyph colour={colour} />}
      </button>
      {(status.kind === "ok" || status.kind === "err") && (
        <span role="status" style={{ position: "absolute", right: 0, bottom: "calc(100% + 4px)", zIndex: 10, whiteSpace: "nowrap", background: "var(--surface-raised)", border: `1px solid ${colour}`, color: "var(--text)", padding: "2px var(--space-2)", borderRadius: "var(--radius-sm)", fontFamily: "var(--font-sans)", fontSize: "var(--fs-xs)", boxShadow: "var(--shadow-1)", pointerEvents: "none" }}>{status.msg}</span>
      )}
    </span>
  );
}
