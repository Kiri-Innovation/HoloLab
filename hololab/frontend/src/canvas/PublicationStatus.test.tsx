import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { PublicationStatus, PublishedAsNotes } from "./PublicationStatus";
import type { CatalogPack } from "../wire";

describe("registry evidence", () => {
  it("does not pretend installation metadata is current central state", () => {
    const pack = { name: "kiri/example", version: "0.0.1", publication: {
      yanked: false, publisher: "tester", source: "file:///registry", code_sha256: "abc",
    } } as unknown as CatalogPack;
    const html = renderToStaticMarkup(<PublicationStatus pack={pack} />);
    expect(html).toContain("安装时记录");
    expect(html).toContain("查询当前中央状态");
    expect(html).toContain("file:///registry");
  });
  it("marks dev release links as annotations rather than history rewrites", () => {
    const html = renderToStaticMarkup(<PublishedAsNotes notes={[{
      name: "kiri/example", version: "0.0.1", source: "file:///registry", code_sha256: "abc",
    }]} />);
    expect(html).toContain("published_as");
    expect(html).toContain("不改历史");
  });
});
