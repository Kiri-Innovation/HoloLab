import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { NodeRunControl, canStopNode } from "./NodeRunControl";
it.each(["pending", "assigned", "running", "orphaned"])("offers stop for %s", state => {
  expect(canStopNode(state)).toBe(true);
  const html=renderToStaticMarkup(<NodeRunControl state={state} jobId="parent" pending={false} onRun={()=>{}}/>);
  expect(html).toContain('aria-label="停止运行"');expect(html).toContain('<rect');expect(html).not.toContain('disabled=""');
});
it.each([undefined,"done","failed","cancelled","interrupted","future"])("offers run for %s", state => {
  expect(canStopNode(state)).toBe(false);
  const html=renderToStaticMarkup(<NodeRunControl state={state} pending={false} onRun={()=>{}}/>);
  expect(html).toContain('aria-label="运行"');expect(html).toContain('<path');
});
it("blocks cancellation while the job identity is missing",()=>{
  const html=renderToStaticMarkup(<NodeRunControl state="running" pending={false} onRun={()=>{}}/>);
  expect(html).toContain('disabled=""');expect(html).toContain('正在获取任务信息');
});
