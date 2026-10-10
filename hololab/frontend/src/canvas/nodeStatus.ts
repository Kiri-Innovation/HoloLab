import type { NodeStaleness } from "./staleness";

export type StatusTone = "neutral" | "info" | "success" | "warning" | "error";
export interface NodeStatusPresentation {
  tone: StatusTone;
  conclusion: string;
  reason: string;
  action: string;
}
export interface NodeStatusInput {
  state?: string;
  stale?: NodeStaleness | null;
  /** Separate input also defines precedence for defensive/future combinations. */
  drift?: boolean;
  failReason?: string | null;
  readOnly?: boolean;
}

/** Presentation only: never mutate runtime, scheduling, or shared job colours. */
export function nodeStatus({ state, stale, drift = false, failReason, readOnly = false }: NodeStatusInput): NodeStatusPresentation {
  const known = !state || ["pending", "assigned", "running", "done", "failed", "cancelled", "orphaned", "interrupted"].includes(state);
  const oldParams = !readOnly && (drift || stale?.kind === "inflight_old_params");
  const changed = !readOnly && !!stale;
  let result: NodeStatusPresentation;
  if (!known) {
    result = { tone: "neutral", conclusion: "状态暂不可识别", reason: `收到状态：${state}`, action: "请查看任务详情，暂不判断结果是否有效。" };
  } else if (state === "failed") {
    result = { tone: "error", conclusion: "运行失败", reason: failReason || "暂未提供失败原因。", action: "查看日志，处理原因后重试。" };
  } else if (state === "orphaned") {
    result = { tone: "error", conclusion: "执行连接丢失，状态待确认", reason: "与执行设备的连接已丢失，任务可能仍在运行。", action: "先检查连接并等待状态同步，避免重复提交。" };
  } else if (state === "interrupted") {
    result = { tone: "error", conclusion: "运行已中断", reason: "本次任务已进入中断终态。", action: "确认设备及残留进程状态后重新运行。" };
  } else if (oldParams) {
    const label = state === "pending" ? "等待中的任务使用旧参数" : state === "assigned" ? "已分配的任务使用旧参数" : state === "running" ? "正在使用旧参数运行" : "已提交任务与当前草稿不同";
    result = { tone: "warning", conclusion: label, reason: stale?.title || "当前草稿与已提交任务的配置不同。", action: "草稿修改不会更新此任务；确认任务结束后，按新配置重新运行。" };
  } else if (!state || state === "cancelled") {
    result = state === "cancelled"
      ? { tone: "neutral", conclusion: "已取消", reason: "本次任务已收到停止指令，未成功完成。", action: "如需完成此节点，请重新运行；这不是暂停。" }
      : { tone: "neutral", conclusion: "尚未运行", reason: "尚无此节点的运行记录。", action: "准备好输入后，点击运行。" };
  } else if (changed) {
    const upstream = stale?.kind === "upstream_dirty";
    result = { tone: "warning", conclusion: upstream ? "结果基于旧输入" : "结果需要更新", reason: stale!.title,
      action: upstream ? "先更新上游，再运行此节点。" : "从此节点重新运行。" };
  } else if (state === "done") {
    result = { tone: "success", conclusion: "已完成", reason: "当前未检测到配置或上游变化。", action: "可展开预览查看结果。" };
  } else {
    const labels: Record<string, string> = { pending: "等待执行", assigned: "已分配，等待启动", running: "正在运行" };
    result = { tone: "info", conclusion: labels[state!], reason: state === "pending" ? "任务已提交，尚未开始。" : state === "assigned" ? "任务已分配给执行设备。" : "任务正在执行。",
      action: "可在任务面板查看进度和日志，无需重复提交。" };
  }
  // Keep secondary evidence even when failure/neutral status wins the colour.
  if (!readOnly && known && stale?.title && !result.reason.includes(stale.title)) result.reason += ` ${stale.title}`;
  if (!readOnly && known && oldParams && result.tone === "error" && !stale?.title) result.reason += " 已提交任务与当前草稿不同。";
  if (readOnly) {
    result.conclusion = state === "done" ? "此快照中的运行已完成" : `此快照：${result.conclusion}`;
    if (state === "done") result.reason = "这是快照记录的完成状态，不代表当前草稿结果有效。";
    result.action = "可查看此快照的任务记录；需要重新运行时请返回工作流草稿。";
  }
  return result;
}
