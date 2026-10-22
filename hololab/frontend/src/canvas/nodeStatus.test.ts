import { expect, it } from "vitest";
import { nodeStatus, statusReasonLines } from "./nodeStatus";
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
  cancelled: ["neutral", "neutral", "neutral", "neutral"],
  orphaned: ["error", "error", "error", "error"],
  interrupted: ["error", "error", "error", "error"],
  future: ["neutral", "neutral", "neutral", "neutral"],
};
for (const state of states) for (const [index, kind] of kinds.entries()) for (const drift of [false, true]) {
  it(`${state ?? 'idle'} / ${kind ?? 'fresh'} / drift=${drift}`, () => {
    const stale: NodeStaleness | undefined = kind ? { kind, title: "具体变化原因" } : undefined;
    const frozen = Object.freeze(stale ?? {});
    const result = nodeStatus({ state, stale, drift });
    const expected = drift && !["failed", "orphaned", "interrupted", "cancelled", "future"].includes(state ?? "") ? "warning" : rows[state ?? "idle"][index];
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

it("separates update reasons and preserves single reasons without blank rows", () => {
  expect(statusReasonLines("算法已更改：a@1 → a@2 · 参数已改：width · 并行度：4 → 5")).toEqual(["算法已更改：a@1 → a@2", "参数已改：width", "并行度：4 → 5"]);
  expect(statusReasonLines("\n 磁盘空间不足。\n\n")).toEqual(["磁盘空间不足。"]);
  expect(statusReasonLines("名字a·b")).toEqual(["名字a·b"]);
  expect(statusReasonLines(" ")).toEqual([]);
});
it("separates failure evidence from each stale reason", () => {
  const result=nodeStatus({state:"failed",failReason:"磁盘空间不足",stale:{kind:"self_dirty",title:"参数已改：width · 并行度：4 → 5"}});
  expect(statusReasonLines(result.reason)).toEqual(["磁盘空间不足", "参数已改：width", "并行度：4 → 5"]);
});

it("keeps stop intent distinct and translates recorded failure categories", () => {
  expect(nodeStatus({state:"cancelled",drift:true}).conclusion).toBe("已主动停止");
  expect(nodeStatus({state:"failed",failReason:"algo_error"}).reason).toBe("算法进程异常退出。");
  expect(nodeStatus({state:"failed",failReason:"system_error"}).reason).toContain("系统");
  expect(nodeStatus({state:"interrupted"}).reason).toContain("未记录具体原因");
});

it('missing required inputs override a healthy result without changing drift', () => {
  const stale = Object.freeze({kind: 'self_dirty' as const, title:'连线已改变'});
  const status = nodeStatus({state:'done', stale, missingInputs:['transforms']});
  expect(status.tone).toBe('neutral');
  expect(status.conclusion).toBe('当前不可运行');
  expect(status.reason).toContain('transforms');
  expect(status.reason).toContain(stale.title);
  expect(nodeStatus({state:'done', missingInputs:[]}).tone).toBe('success');
  expect(nodeStatus({state:'done', readOnly:true, missingInputs:['transforms']}).tone).toBe('success');
});
it.each(['failed','orphaned','running','pending','assigned'])('preserves %s while noting missing inputs', state => {
  const original = nodeStatus({state});
  const blocked = nodeStatus({state, missingInputs:['transforms']});
  expect(blocked.tone).toBe(original.tone);
  expect(blocked.reason).toContain('transforms');
});
