import { expect, it } from "vitest";
import { nodeStatus } from "./nodeStatus";
import type { NodeStaleness } from "./staleness";

const states = [undefined, "pending", "assigned", "running", "done", "failed", "cancelled", "orphaned", "interrupted", "future"];
const kinds = [undefined, "self_dirty", "upstream_dirty", "inflight_old_params"] as const;
const rows: Record<string, string[]> = {
  idle: ["neutral", "neutral", "neutral", "warning"],
  pending: ["info", "warning", "warning", "warning"],
  assigned: ["info", "warning", "warning", "warning"],
  running: ["info", "warning", "warning", "warning"],
  done: ["success", "warning", "warning", "warning"],
  failed: ["error", "error", "error", "error"],
  cancelled: ["neutral", "neutral", "neutral", "warning"],
  orphaned: ["error", "error", "error", "error"],
  interrupted: ["error", "error", "error", "error"],
  future: ["neutral", "neutral", "neutral", "neutral"],
};
for (const state of states) for (const [index, kind] of kinds.entries()) for (const drift of [false, true]) {
  it(`${state ?? 'idle'} / ${kind ?? 'fresh'} / drift=${drift}`, () => {
    const stale: NodeStaleness | undefined = kind ? { kind, title: "具体变化原因" } : undefined;
    const frozen = Object.freeze(stale ?? {});
    const result = nodeStatus({ state, stale, drift });
    const expected = drift && !["failed", "orphaned", "interrupted", "future"].includes(state ?? "") ? "warning" : rows[state ?? "idle"][index];
    expect(result.tone).toBe(expected);
    expect(result.conclusion).toBeTruthy();expect(result.reason).toBeTruthy();expect(result.action).toBeTruthy();
    if (stale && state !== "future") expect(result.reason).toContain(stale.title);
    expect(stale ?? {}).toEqual(frozen);
  });
}
it("preserves failure evidence and distinguishes the three red states", () => {
  expect(nodeStatus({ state: "failed", failReason: "磁盘已满" }).reason).toContain("磁盘已满");
  expect(new Set(["failed", "orphaned", "interrupted"].map(state => nodeStatus({ state }).conclusion)).size).toBe(3);
  expect(nodeStatus({ state: "orphaned" }).action).toContain("避免重复提交");
});
it("describes historical completion without claiming freshness", () => {
  const result=nodeStatus({ state:"done", stale:{kind:"self_dirty",title:"参数已改"},drift:true,readOnly:true });
  expect(result.tone).toBe("success");expect(result.conclusion).toBe("此快照中的运行已完成");
  expect(result.reason).not.toContain("未检测到");expect(result.reason).not.toContain("参数已改");
});
it("unknown status stays neutral even with drift", () => {
  expect(nodeStatus({state:"future",drift:true}).conclusion).toBe("状态暂不可识别");
});
