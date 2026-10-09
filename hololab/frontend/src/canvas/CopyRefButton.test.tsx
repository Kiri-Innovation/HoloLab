import { afterEach, expect, it, vi } from "vitest";
import { useState } from "react";
import { CopyRefButton } from "./CopyRefButton";
import { flopsInsertReference, flopsInsertReferenceAvailable } from "../flops";

vi.mock("react", () => ({ useState: vi.fn((initial) => [initial, vi.fn()]) }));
vi.mock("../flops", () => ({ flopsInsertReference: vi.fn(), flopsInsertReferenceAvailable: vi.fn() }));

afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); });

it("inserts a workflow using the instance canvas URL, custom display and shared snapshot", async () => {
  vi.stubGlobal("window", { location: { origin: "http://lan-host:8123" }, setTimeout: vi.fn() });
  vi.mocked(flopsInsertReferenceAvailable).mockReturnValue(true);
  vi.mocked(flopsInsertReference).mockResolvedValue({ success: true });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ kind: "workflow", ref: "hololab://workflow/w1", resource: { workflow_id: "w1", name: "Demo", graph: { nodes: [], edges: [] } } }) }));
  const button = CopyRefButton({ kind: "workflow", id: "w1", referenceDisplay: { title: "Demo", subtitle: "0 个节点" } });
  await button.props.children[0].props.onClick({ stopPropagation: vi.fn() });
  expect(flopsInsertReference).toHaveBeenCalledWith(expect.objectContaining({
    provider: "hololab", type: "workflow", resource: { uri: "http://lan-host:8123/w/w1" },
    display: { title: "Demo", subtitle: "0 个节点", icon: "workflow" },
    snapshot: { text: expect.stringContaining('"workflow_id": "w1"'), mediaType: "text/markdown" },
  }));
});

it("copies the workflow token in an ordinary browser without fetching", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  const fetch = vi.fn();
  vi.stubGlobal("window", { isSecureContext: true, setTimeout: vi.fn() });
  vi.stubGlobal("navigator", { clipboard: { writeText } });
  vi.stubGlobal("fetch", fetch);
  vi.mocked(flopsInsertReferenceAvailable).mockReturnValue(false);
  const button = CopyRefButton({ kind: "workflow", id: "w1", comment: "Demo · 0 个节点" });
  await button.props.children[0].props.onClick({ stopPropagation: vi.fn() });
  expect(writeText).toHaveBeenCalledWith("hololab://workflow/w1  # Demo · 0 个节点");
  expect(fetch).not.toHaveBeenCalled();
  expect(flopsInsertReference).not.toHaveBeenCalled();
});

it("shows the failure reason and copied feedback beside the button", () => {
  vi.mocked(useState).mockReturnValueOnce([{ state: "copied", message: "插入失败：busy。已复制，可粘贴到对话" }, vi.fn()]);
  const view = CopyRefButton({ kind: "handle", id: "a" });
  const [button, status] = view.props.children;
  expect(button.props.title).toBe("插入失败：busy。已复制，可粘贴到对话");
  expect(status.props.role).toBe("status");
  expect(status.props.children[0]).toBe(button.props.title);
});
it("offers an explicit copy retry beside a double failure", () => {
  vi.mocked(useState).mockReturnValueOnce([{ state: "err", message: "插入失败：busy。复制失败", retryText: "hololab://handle/a" }, vi.fn()]);
  const view = CopyRefButton({ references: [{ kind: "handle", id: "a" }] });
  expect(view.props.children[1].props.children[1].props.children).toBe("重试复制");
});
