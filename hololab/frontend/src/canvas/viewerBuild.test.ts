import { expect, it } from "vitest";
import { versionedViewerUrl } from "./viewerBuild";

it("preserves embedded source, HUD and fragment while replacing the build key", () => {
  const url = versionedViewerUrl("/colmaputil/index.html?embed=splatv&src=%2Fproxy%2Fmodel%3Fx%3D1&hud=1&b=old#view", "new");
  const parsed = new URL(url, "http://test");
  expect(parsed.searchParams.get("src")).toBe("/proxy/model?x=1");
  expect(parsed.searchParams.get("embed")).toBe("splatv");
  expect(parsed.searchParams.get("hud")).toBe("1");
  expect(parsed.searchParams.getAll("b")).toEqual(["new"]);
  expect(parsed.hash).toBe("#view");
});

it("changes the URL across builds, but not across renders within a build", () => {
  const src = "/pack-assets/node/stg-to-splatv/0.1.0/viewers/splatv/index.html";
  expect(versionedViewerUrl(src, "a")).not.toBe(versionedViewerUrl(src, "b"));
  expect(versionedViewerUrl(src)).toBe(versionedViewerUrl(src));
});
