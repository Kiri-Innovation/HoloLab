import { useState } from "react";
import type { CatalogPack, PublicationNote } from "../wire";

export function PublishedAsNotes({ notes }: { notes?: PublicationNote[] }) {
  return <>{notes?.map(note => <div key={`${note.name}@${note.version}`}>
    published_as：{note.name}@{note.version} · 发布关联（不改历史）<br />
    来源：{note.source} · SHA-256：{note.code_sha256}
  </div>)}</>;
}

export function PublicationStatus({ pack, nodeId }: { pack: CatalogPack; nodeId?: string | null }) {
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function refresh() {
    if (!nodeId) return;
    setBusy(true); setError("");
    try {
      const query = new URLSearchParams({ name: pack.name, version: pack.version });
      const response = await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/pack-status?${query}`);
      if (!response.ok) throw new Error(await response.text());
      setResult(await response.json() as Record<string, unknown>);
    } catch (reason) { setError(String(reason)); }
    finally { setBusy(false); }
  }
  const publication = (result?.publication ?? pack.publication) as Record<string, unknown> | undefined;
  const status = result?.state === "hash_mismatch" ? "哈希不匹配 · 必须停止"
    : result?.registry_status === "yanked" ? "已 yank · 禁止新安装"
    : result?.registry_status === "published" ? "已发布"
    : result?.registry_status === "unknown" ? "中央状态未知 · 本地仍可用"
    : publication ? "已发布（安装时记录）" : "发布状态未查询";
  return <details className="nodrag" style={{ position: "relative", fontSize: "var(--fs-micro)" }}>
    <summary>{status}</summary>
    <div style={{ position: "absolute", zIndex: 20, width: 340, padding: 10,
      background: "var(--surface-2)", border: "1px solid var(--border)", overflowWrap: "anywhere" }}>
      <div>来源：{String(result?.source ?? publication?.source ?? "本地包；尚未确认中央登记")}</div>
      {publication && <><div>发布者：{String(publication.publisher ?? "未记录")}</div>
        <div>代码身份：{String(publication.code_sha256 ?? "未记录")}</div></>}
      <div>yank 不删除包体，不阻止已安装或历史版本解析。</div>
      {result?.message != null && <p>{String(result.message)}</p>}
      {result?.registry_message != null && <p>{String(result.registry_message)}</p>}
      {error && <p role="alert">状态查询失败：{error}</p>}
      <button disabled={!nodeId || busy} onClick={() => void refresh()}>{busy ? "查询中…" : "查询当前中央状态"}</button>
    </div>
  </details>;
}
