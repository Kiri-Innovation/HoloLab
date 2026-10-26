import { useEffect, useRef, useState, type ReactNode } from "react";
import './workflowEvidence.css';

export function RecipeFileActions({workflowId, beforeExport, onOpen, onReload, onSave, compact = false}: {
  compact?: boolean; workflowId?: string | null; beforeExport?: () => Promise<void>;
  onOpen: (id: string) => void; onReload?: () => Promise<void>; onSave?: () => Promise<void>;
}) {
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const act = async (task: () => Promise<void>, success: string, failure: string) => {
    setBusy(true); setError(""); setStatus("");
    try { await task(); setStatus(success); } catch { setError(failure); }
    finally { setBusy(false); }
  };
  const content = <span className="hl-recipe-actions" aria-busy={busy}>
    {workflowId && <button disabled={busy} onClick={() => void act(async () => {
      await beforeExport?.();
      const response = await fetch(`/api/workflows/${encodeURIComponent(workflowId)}/recipe`);
      if (!response.ok) throw Error();
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a"); link.href = url; link.download = "recipe.json"; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }, "配方已导出。", "未能导出配方，请确认草稿已保存后重试。")}>导出配方</button>}
    <button className="hl-evidence-primary" disabled={busy} onClick={() => fileInput.current?.click()}>从配方文件新建</button>
    <input ref={fileInput} aria-label="从配方文件新建工作流" type="file" accept=".json,application/json" hidden disabled={busy}
      onChange={e => { const file = e.target.files?.[0]; if (!file) return;
        void act(async () => {
          const response = await fetch("/api/recipes/import", {method:"POST",headers:{"Content-Type":"application/json"},body:await file.text()});
          if (!response.ok) throw Error();
          onOpen((await response.json()).workflow_id);
        }, "已新建工作流；请在 Pack Manager 的使用情况中查看待处理节点。", "未能导入，请确认文件是有效的配方 JSON，并检查连接后重试。");
        e.target.value = "";
      }} />
    {onSave && <button disabled={busy} onClick={() => void act(onSave, "配方文件已保存。", "保存未完成，请检查连接后重试。")}>保存配方文件</button>}
    {onReload && <button disabled={busy} onClick={() => {
      if (window.confirm("从文件重新载入工作流？当前未保存的编辑将被替换。"))
        void act(onReload, "已从文件重新载入。", "重载未完成，请检查文件与连接后重试。");
    }}>从文件重载</button>}
    {busy && <span role="status">正在处理配方…</span>}
    {status && <span role="status">{status}</span>}
    {error && <span role="alert">{error}</span>}
  </span>;
  return compact ? <RecipeMenu>{content}</RecipeMenu> : content;
}

function RecipeMenu({children}: {children: ReactNode}) {
  const menu = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const closeOutside = (event: PointerEvent) => {
      if (menu.current && !menu.current.contains(event.target as Node)) menu.current.open = false;
    };
    document.addEventListener("pointerdown", closeOutside);
    return () => document.removeEventListener("pointerdown", closeOutside);
  }, []);
  return <details ref={menu} className="hl-recipe-menu" onKeyDown={event => {
    if (event.key === "Escape" && menu.current) {
      menu.current.open = false; menu.current.querySelector("summary")?.focus();
    }
  }}><summary>配方文件</summary><div>{children}</div></details>;
}
