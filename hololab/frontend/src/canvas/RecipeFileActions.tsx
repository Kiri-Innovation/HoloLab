import { useState } from "react";

export function RecipeFileActions({workflowId, beforeExport, onOpen, onReload, onSave}: {
  workflowId?: string | null; beforeExport?: () => Promise<void>;
  onOpen: (id: string) => void; onReload?: () => Promise<void>; onSave?: () => Promise<void>;
}) {
  const [error, setError] = useState("");
  const act = async (task: () => Promise<void>) => { try { setError(""); await task(); } catch (e) { setError(String(e)); } };
  return <span style={{display: "inline-flex", gap: 8, alignItems: "center"}}>
    {workflowId && <button onClick={() => void act(async () => {
      await beforeExport?.();
      const response = await fetch(`/api/workflows/${encodeURIComponent(workflowId)}/recipe`);
      if (!response.ok) throw Error(await response.text());
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a"); link.href = url; link.download = "recipe.json"; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    })}>导出配方</button>}
    <label>从配方文件新建<input aria-label="从配方文件新建工作流" type="file" accept=".json,application/json" style={{width: 120}}
      onChange={e => { const file = e.target.files?.[0]; if (!file) return;
        void act(async () => { const response = await fetch("/api/recipes/import", {method:"POST",headers:{"Content-Type":"application/json"},body:await file.text()});
          if (!response.ok) throw Error(await response.text()); onOpen((await response.json()).workflow_id);
        }); e.target.value = "";
      }} /></label>
    {onSave && <button onClick={() => void act(onSave)}>保存配方文件</button>}
    {onReload && <button onClick={() => void act(onReload)}>从文件重载</button>}
    {error && <span role="alert">{error}</span>}
  </span>;
}
