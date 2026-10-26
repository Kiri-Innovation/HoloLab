import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { IdentityEvidence } from "./CodeIdentityPanel";

it("labels missing historical evidence without inventing a digest", () => {
  const html = renderToStaticMarkup(<IdentityEvidence identity={null} />);
  expect(html).toContain("历史运行未记录代码身份");
  expect(html).toContain("不会用当前代码回填");
  expect(html).not.toContain("SHA-256");
});

it("separates verified hot content from cold baseline and exposes frozen evidence", () => {
  const html = renderToStaticMarkup(<IdentityEvidence identity={{
    format: 2, sha256: "combined", source: "demo@0.1.1+",
    hot: { sha256: "hot-digest", verification: "verified_at_submission" },
    cold: { sha256: "cold-digest", baseline_at: 1700000000, baseline_status: "updated" },
    manifest: { sha256: "normalized", raw_sha256: "raw-manifest" },
    git: { dirty: true, commit: "git-commit", tree_sha: "git-tree" },
    ignored: { files: 3, links: 1, bytes: 123, top_level_names: ["assets"] },
    uncovered: [{ module: "native-extension" }], undeclared: [{ path: "/outside" }],
    effective_params: { iterations: 12 },
  }} />);
  for (const expected of ["combined", "hot-digest", "cold-digest", "normalized", "raw-manifest",
    "提交时已校验", "仅记录基线", "基线已更新", "含未提交改动", "git-tree", "assets",
    "native-extension", "/outside", "iterations", "12"]) expect(html).toContain(expected);
  expect(html).not.toContain("代码已固定");
});

it("renders frozen values structurally and keeps raw records out of normal UI", () => {
  const html = renderToStaticMarkup(<IdentityEvidence identity={{
    sha256: "a".repeat(64), uncovered: [{path: "/native/renderer.so", declared: false}],
    effective_params: {iterations: 24, flags: [true, false]},
  }} />);
  expect(html).toContain("<dl");
  expect(html).toContain("展开完整值");
  expect(html).toContain("/native/renderer.so");
  expect(html).not.toContain("<pre");
  expect(html).not.toContain("完整冻结记录");
});
