import { flopsInsertReference, flopsInsertReferenceAvailable, type FlopsInsertReferenceParams } from "../flops";
import { referenceSnapshotDocument, resolveReference } from "./referenceSnapshot";

export type RefKind =
  | "workflow"
  | "run"
  | "job"
  | "handle"
  | "pack"
  | "node"
  | "graph-node"
  // Index / collection kinds — they name a *page*, not a resource, so
  // they carry NO id. See ``hololab.gateway.refs._INDEX_KINDS``.
  | "workflows"
  | "artifacts";

export interface ReferenceInput {
  kind: RefKind;
  id: string;
  comment?: string;
  referenceDisplay?: { title: string; subtitle: string };
}

export interface ReferenceOutcome {
  state: "inserted" | "copied" | "err";
  message: string;
  retryText?: string;
  retryPrefix?: string;
}

export function formatToken(kind: RefKind, id: string, comment?: string): string {
  // Index kinds render without the ``/id`` segment — the token is
  // ``hololab://workflows`` on its own. Every other kind carries the id.
  const base = id ? `hololab://${kind}/${id}` : `hololab://${kind}`;
  return comment ? `${base}  # ${comment}` : base;
}

function truncateSnapshot(text: string): string {
  const limit = 60 * 1024;
  const bytes = new TextEncoder().encode(text);
  if (bytes.length <= limit) return text;
  return `${new TextDecoder().decode(bytes.slice(0, limit - 32))}\n\n_摘要已截断。_`;
}

export async function writeToClipboard(text: string): Promise<boolean> {
  // navigator.clipboard requires a secure context; fall back to the
  // document.execCommand path so the button still works over plain http
  // (which is exactly how the LAN case runs).
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      /* fall through to the legacy path */
    }
  }
  let ta: HTMLTextAreaElement | undefined;
  try {
    ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    ta?.remove();
  }
}

async function insertOne({ kind, id, comment, referenceDisplay }: ReferenceInput) {
  const token = formatToken(kind, id, comment);
  const path = kind === "workflow" ? `w/${id}` : id ? `${kind}/${id}` : kind;
  // The resolver DTO is the one snapshot source for every UI anchor.
  // If the gateway is temporarily unavailable, retain a useful, clearly
  // limited reference rather than silently claiming a full resource view.
  let snapshotText: string;
  try {
    snapshotText = referenceSnapshotDocument(await resolveReference(token));
  } catch {
    snapshotText = referenceSnapshotDocument({
      kind,
      ref: token,
      resource: { resolver_status: "unavailable" },
      related: {},
    });
  }
  const payload: FlopsInsertReferenceParams = {
    schemaVersion: 1,
    provider: "hololab",
    type: kind === "graph-node" ? "workflow_node" : kind.replace(/-/g, "_"),
    // This is the actual running instance, never the old marketing host.
    resource: { uri: new URL(`/${path}`, window.location.origin).toString() },
    display: {
      title: referenceDisplay?.title ?? `HoloLab ${kind}`,
      subtitle: referenceDisplay?.subtitle ?? comment ?? token,
      icon: kind === "graph-node" || kind === "workflow" ? "workflow" : "external",
    },
    access: { mode: "link_only" },
    snapshot: {
      text: truncateSnapshot(snapshotText),
      mediaType: "text/markdown",
    },
  };
  return flopsInsertReference(payload);
}

export async function copyReferenceText(text: string, prefix = ""): Promise<ReferenceOutcome> {
  const ok = await writeToClipboard(text);
  return ok
    ? { state: "copied", message: `${prefix}已复制，可粘贴到对话` }
    : { state: "err", message: `${prefix}复制失败：浏览器未允许写入剪贴板，请重试复制`, retryText: text, retryPrefix: prefix };
}

export async function sendReferences(references: ReferenceInput[]): Promise<ReferenceOutcome> {
  if (references.length === 0) return { state: "err", message: "请先选择引用" };
  if (!flopsInsertReferenceAvailable()) {
    return copyReferenceText(references.map(r => formatToken(r.kind, r.id, r.comment)).join("\n"));
  }
  // The declared v1 API accepts one reference, with no batch/relationship field.
  // Preserve each resource's identity; don't invent a synthetic collection.
  for (let i = 0; i < references.length; i++) {
    let reason: string;
    try {
      const result = await insertOne(references[i]);
      if (result.success) continue;
      reason = result.reason || "接口未提供原因";
    } catch (error) {
      reason = error instanceof Error ? error.message : String(error);
    }
    // A failed insertion must still give the user usable reference text.
    // Stop at the first failure and copy only the unconfirmed remainder so
    // confirmed insertions aren't duplicated. A thrown response can be
    // ambiguous: never retry insertion automatically.
    const remaining = references.slice(i).map(r => formatToken(r.kind, r.id, r.comment)).join("\n");
    return copyReferenceText(remaining,
      `已插入 ${i}/${references.length} 条；第 ${i + 1} 条插入失败：${reason}。剩余 ${references.length - i} 条：`);
  }
  return { state: "inserted", message: `已插入 Flops 输入框（${references.length} 条）` };
}
