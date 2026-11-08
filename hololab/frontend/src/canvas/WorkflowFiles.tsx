import { useEffect, useState } from "react";
import { PathInput } from "./PathInput";
import { CONTROL_STYLE } from "../ui/controlStyles";

export async function workflowFileRequest(url: string, body?: unknown) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) {
    if (
      data.detail?.code === "workflow_id_mismatch" &&
      window.confirm(
        `路径中的文件 ID 是 ${data.detail.id}，与记忆的 ${data.detail.expected_id} 不同。作为新文件打开并更新此路径关联？取消可重新定位原工作流。`,
      )
    )
      return workflowFileRequest(url, {
        ...(body as object),
        replace_id: data.detail.expected_id,
      });
    throw Error(
      typeof data.detail === "string"
        ? data.detail
        : JSON.stringify(data.detail),
    );
  }
  return data as { id: string; path: string };
}
export function WorkflowFilePicker({
  mode,
  onOpen,
  onClose,
}: {
  mode: "create" | "open";
  onOpen: (id: string) => void;
  onClose: () => void;
}) {
  const [path, setPath] = useState("/cloud/cloud-ssd1/Kiri4DGS");
  const [name, setName] = useState("untitled");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <div
      role="dialog"
      aria-label={
        mode === "create" ? "新建 workflow 文件" : "打开 workflow 文件"
      }
      style={{
        background: "var(--bg)",
        padding: 16,
        border: "1px solid var(--border)",
      }}
    >
      <label>
        {mode === "create" ? "目录" : "文件路径"}
        <PathInput
          nodeId="gateway"
          name="workflow-path"
          value={path}
          onChange={setPath}
          pathKind={mode === "create" ? "directory" : "file"}
        />
      </label>
      {mode === "create" && (
        <label>
          文件名
          <input
            aria-label="Workflow 文件名"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
      )}
      <button
        style={CONTROL_STYLE}
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          setError("");
          try {
            if (mode === "open" && !path.endsWith(".workflow"))
              throw Error("请选择 .workflow 文件");
            const result = await workflowFileRequest(
              mode === "create"
                ? "/api/workflow-files"
                : "/api/workflow-files/open",
              mode === "create" ? { parent_dir: path, name } : { path },
            );
            onOpen(result.id);
            onClose();
          } catch (e) {
            setError(String(e));
          } finally {
            setBusy(false);
          }
        }}
      >
        {mode === "create" ? "创建" : "打开"}
      </button>
      <button style={CONTROL_STYLE} onClick={onClose}>
        取消
      </button>
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
export function WorkflowFileRecovery({
  id,
  onOpen,
  mismatch = false,
}: {
  id: string;
  onOpen: (id: string) => void;
  mismatch?: boolean;
}) {
  const [path, setPath] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    let disposed = false;
    fetch("/api/workflows")
      .then((r) => r.json())
      .then((rows) => {
        const known = Array.isArray(rows)
          ? rows.find((row) => row.workflow_id === id)
          : null;
        if (!disposed && known?.path) setPath(known.path);
      })
      .catch(() => {});
    return () => {
      disposed = true;
    };
  }, [id]);
  return (
    <div
      role="dialog"
      aria-label="Workflow 文件丢失或身份不匹配"
      style={{
        background: "var(--bg)",
        padding: 16,
        border: "1px solid var(--border)",
      }}
    >
      <p>
        {mismatch
          ? "路径中的文件属于不同的 ID。请重新定位原资源，或作为不同文件打开。"
          : "Workflow 文件丢失。请选择重新定位，或在原路径新建空 workflow。"}
      </p>
      <PathInput
        nodeId="gateway"
        name="workflow-relink"
        value={path}
        onChange={setPath}
        pathKind="file"
      />
      <button
        onClick={async () => {
          try {
            await workflowFileRequest(
              `/api/workflows/${encodeURIComponent(id)}/relink`,
              { path },
            );
            onOpen(id);
          } catch (e) {
            setError(String(e));
          }
        }}
      >
        重新定位（relink）
      </button>
      {!mismatch && (
        <button
          onClick={async () => {
            try {
              await workflowFileRequest(
                `/api/workflows/${encodeURIComponent(id)}/recreate`,
              );
              onOpen(id);
            } catch (e) {
              setError(String(e));
            }
          }}
        >
          原路径新建空 wf
        </button>
      )}
      {mismatch && (
        <button
          onClick={async () => {
            try {
              if (!path.endsWith(".workflow"))
                throw Error("请选择 .workflow 文件");
              const result = await workflowFileRequest(
                "/api/workflow-files/open",
                { path },
              );
              onOpen(result.id);
            } catch (e) {
              setError(String(e));
            }
          }}
        >
          作为不同文件打开
        </button>
      )}
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
