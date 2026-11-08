import { newId } from "../newId";
import type { WorkflowOp } from "./workflowPatch";

/** One-shot file edits for gallery/restore actions outside the live canvas. */
export function editWorkflowFile(
  id: string,
  rev: number,
  ops: WorkflowOp[],
): Promise<void> {
  return new Promise((resolve, reject) => {
    const request_id = newId();
    let current = rev;
    let accepted = false;
    const ws = new WebSocket(
      `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/frontend`,
    );
    const finish = (error?: Error) => {
      clearTimeout(timer);
      ws.onclose = null;
      ws.close();
      error ? reject(error) : resolve();
    };
    const timer = setTimeout(() => finish(Error("保存超时")), 30000);
    const send = () =>
      ws.send(
        JSON.stringify({
          kind: "workflow_edit",
          payload: { id, base_rev: current, ops, request_id },
        }),
      );
    ws.onopen = send;
    ws.onerror = () => finish(Error("文件编辑连接失败"));
    ws.onclose = () => finish(Error("文件编辑连接已断开"));
    ws.onmessage = (event) => {
      const { kind, payload: p } = JSON.parse(event.data);
      if (kind !== "workflow_changed" || p.id !== id) return;
      if (p.error && !p.rejected) {
        finish(Error(p.error));
        return;
      }
      if (p.request_id === request_id) {
        if (p.rejected) {
          if (p.error === "base_rev is behind current revision") {
            current = p.rev;
            send();
          } else finish(Error(p.error));
        } else {
          current = p.rev;
          accepted = true;
        }
      }
      if (accepted && p.saved && p.rev >= current) finish();
    };
  });
}
