import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { PathInput } from "./PathInput";
import { CONTROL_STYLE, PRIMARY_CONTROL_STYLE } from "../ui/controlStyles";

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

function WorkflowFileModal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title}
      data-hl-workflow-file-modal=""
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1200,
        background: "rgba(0, 0, 0, 0.65)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "var(--space-6)",
      }}
    >
      <div
        onClick={(event) => event.stopPropagation()}
        style={{
          width: "100%",
          maxWidth: 720,
          maxHeight: "80vh",
          overflow: "auto",
          boxSizing: "border-box",
          padding: "var(--space-5)",
          background: "var(--overlay)",
          color: "var(--text-body)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius-lg)",
          boxShadow: "var(--shadow-2)",
        }}
      >
        {children}
      </div>
    </div>
  );
}

const FIELD_STYLE = {
  display: "grid",
  gap: "var(--space-2)",
  color: "var(--text-body)",
  fontSize: "var(--fs-sm)",
};

const NAME_INPUT_STYLE = {
  width: "100%",
  height: "var(--control-h-md)",
  boxSizing: "border-box" as const,
  padding: "0 var(--space-2)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-sm)",
  background: "var(--surface-2)",
  color: "var(--text-body)",
  fontSize: "var(--fs-sm)",
};

const ACTIONS_STYLE = {
  display: "flex",
  justifyContent: "flex-end",
  gap: "var(--space-2)",
  marginTop: "var(--space-5)",
};

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

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy) return;
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
  };

  return (
    <WorkflowFileModal
      title={mode === "create" ? "新建 workflow 文件" : "打开 workflow 文件"}
      onClose={onClose}
    >
      <form onSubmit={submit}>
        <div style={{ display: "grid", gap: "var(--space-4)" }}>
          <label style={FIELD_STYLE}>
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
            <label style={FIELD_STYLE}>
              文件名
              <input
                aria-label="Workflow 文件名"
                value={name}
                onChange={(e) => setName(e.target.value)}
                style={NAME_INPUT_STYLE}
              />
            </label>
          )}
        </div>
        {error && <p role="alert">{error}</p>}
        <div style={ACTIONS_STYLE}>
          <button type="button" style={CONTROL_STYLE} onClick={onClose} disabled={busy}>
            取消
          </button>
          <button type="submit" style={PRIMARY_CONTROL_STYLE} disabled={busy}>
            {mode === "create" ? "创建" : "打开"}
          </button>
        </div>
      </form>
    </WorkflowFileModal>
  );
}
export function WorkflowFileRecovery({
  id,
  onOpen,
  onClose,
  mismatch = false,
}: {
  id: string;
  onOpen: (id: string) => void;
  onClose: () => void;
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
    <WorkflowFileModal title="Workflow 文件丢失或身份不匹配" onClose={onClose}>
      <p>
        {mismatch
          ? "路径中的文件属于不同的 ID。请重新定位原资源，或作为不同文件打开。"
          : "Workflow 文件丢失。请选择重新定位，或在原路径新建空 workflow。"}
      </p>
      <div style={{ ...FIELD_STYLE, marginTop: "var(--space-4)" }}>
        <PathInput
          nodeId="gateway"
          name="workflow-relink"
          value={path}
          onChange={setPath}
          pathKind="file"
        />
      </div>
      <div style={{ ...ACTIONS_STYLE, flexWrap: "wrap", justifyContent: "flex-start" }}>
        <button style={CONTROL_STYLE} onClick={async () => {
          try {
            await workflowFileRequest(
              `/api/workflows/${encodeURIComponent(id)}/relink`,
              { path },
            );
            onOpen(id);
          } catch (e) {
            setError(String(e));
          }
        }}>重新定位（relink）</button>
      {!mismatch && (
        <button
          style={CONTROL_STYLE}
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
          style={CONTROL_STYLE}
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
        <button type="button" style={CONTROL_STYLE} onClick={onClose}>取消</button>
      </div>
      {error && <p role="alert">{error}</p>}
    </WorkflowFileModal>
  );
}
