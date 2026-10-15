import { Modal } from "../ui/Modal";

export function WorkflowConflictDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  return <Modal open={open} onClose={onClose} title="Workflow changed" maxWidth={560}>
    <p style={{ marginTop: 0, lineHeight: 1.6 }}>
      {"This workflow was modified in another tab (or by an agent) after " +
        "you opened it. Reload the page to see the fresh version; your " +
        "unsaved edits will be lost. (Save is parked until you reload " +
        "to protect the other writer's changes.)"}
    </p>
    <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, flexWrap: "wrap" }}>
      <button type="button" autoFocus onClick={onClose}>暂不刷新</button>
      <button type="button" className="hl-button hl-button--primary" onClick={() => window.location.reload()}>重新加载</button>
    </div>
  </Modal>;
}
