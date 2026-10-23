import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { EnvironmentChecks, EnvironmentEvidence, type EnvironmentReport } from "./EnvironmentChecks";

describe("local capability evidence", () => {
  it("shows missing requirements and remedies instead of readiness", () => {
    const report: EnvironmentReport = { status: "missing", source: "demo@1", node_id: "gpu-node",
      issues: [{ item: "package:mmcv", required: "mmcv>=2", actual: "未安装", remedy: "手动安装后重新检查" }] };
    const html = renderToStaticMarkup(<EnvironmentChecks checks={{ reports: { train: report }, loading: false, error: "", refresh: () => {} }} />);
    for (const text of ["1 项缺失", "train", "gpu-node", "mmcv", "未安装", "手动安装", "只覆盖已声明且在可探测范围内", "通过不代表一定能运行"])
      expect(html).toContain(text);
    expect(html).not.toContain("环境已就绪");
  });
  it("distinguishes legacy unrecorded evidence from undeclared contracts", () => {
    expect(renderToStaticMarkup(<EnvironmentEvidence />)).toContain("环境未校验");
    const html = renderToStaticMarkup(<EnvironmentEvidence report={{ status: "undeclared", issues: [] }} />);
    expect(html).toContain("环境未声明");
    expect(html).not.toContain("环境/硬件缺失");
    expect(html).toContain("环境未冻结");
  });
});
