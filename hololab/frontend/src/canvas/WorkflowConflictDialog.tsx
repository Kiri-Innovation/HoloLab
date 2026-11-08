import { Modal } from "../ui/Modal";

export function WorkflowConflictDialog({ open, onClose, onReload = () => window.location.reload(), summary }: { open: boolean; onClose: () => void; onReload?: () => void; summary?: string }) {
  return <Modal open={open} onClose={onClose} title="Workflow changed" maxWidth={560}>
    <p style={{ marginTop: 0, lineHeight: 1.6 }}>
      并发编辑已按元素合并，本地修改的字段优先。你可以继续编辑，或重载放弃尚未确认的本地编辑。
    </p>
    {summary && <pre style={{whiteSpace:"pre-wrap"}}>{summary}</pre>}
    <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, flexWrap: "wrap" }}>
      <button type="button" autoFocus onClick={onClose}>继续编辑</button>
      <button type="button" className="hl-button hl-button--primary" onClick={onReload}>重载放弃本地</button>
    </div>
  </Modal>;
}
