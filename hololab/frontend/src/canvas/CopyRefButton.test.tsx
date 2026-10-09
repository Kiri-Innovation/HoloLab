import { afterEach, expect, it, vi } from "vitest";
import { useState } from "react";
import { showToast } from "../ui/Toast";
import { CopyRefButton } from "./CopyRefButton";
import { flopsInsertReference, flopsInsertReferenceAvailable } from "../flops";

vi.mock("../ui/Toast", () => ({ showToast: vi.fn() }));
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
  expect(showToast).toHaveBeenCalledWith(expect.objectContaining({ message: "已复制引用", tone: "success" }));
});

it("keeps only insertion diagnostics beside the button after fallback", () => {
  const insertionError = "已插入 0/1 条；插入失败：busy";
  vi.mocked(useState).mockReturnValueOnce([{ state: "copied", message: "已复制引用", insertionError }, vi.fn()]);
  const [button, status] = CopyRefButton({ kind: "handle", id: "a" }).props.children;
  expect(button.props["aria-description"]).toBe(insertionError);
  expect(status.props.role).toBe("status");
  expect(status.props.children).toBe(insertionError);
});
it.each(["copied", "err"])("does not render inline feedback for pure copy result %s", state => {
  vi.mocked(useState).mockReturnValueOnce([{ state, message: "copy result" }, vi.fn()]);
  const view = CopyRefButton({ kind: "handle", id: "a" });
  expect(view.props.children[1]).toBe(false);
});

it.each([
  { available: true, icon: "quote", action: "引用到 Flops" },
  { available: false, icon: "clipboard", action: "复制引用" },
])("uses $icon with matching tooltip and accessible name", ({ available, icon, action }) => {
  vi.mocked(flopsInsertReferenceAvailable).mockReturnValue(available);
  const button = CopyRefButton({ kind: "workflow", id: "w1" }).props.children[0];
  const glyph = button.props.children[0];
  expect(glyph.type).toBe("svg");
  expect(glyph.props["data-reference-icon"]).toBe(icon);
  expect(glyph.props["aria-hidden"]).toBe("true");
  expect(glyph.props.width).toBe(available ? 12 : 14);
  expect(glyph.props.height).toBe(available ? 12 : 14);
  expect(glyph.props.strokeWidth).toBe(available ? 2.2 : 2);
  expect(button.props.title).toBeUndefined();
  expect(button.props["data-tooltip"]).toBe(action);
  expect(button.props["aria-label"]).toBe(action);
});

it("rechecks availability on render for the shared batch button", () => {
  const props = { references: [{ kind: "handle" as const, id: "a" }], label: "引用 1 项" };
  vi.mocked(flopsInsertReferenceAvailable).mockReturnValue(false);
  const before = CopyRefButton(props).props.children[0];
  vi.mocked(flopsInsertReferenceAvailable).mockReturnValue(true);
  const after = CopyRefButton(props).props.children[0];
  expect(before.props.children[0].props["data-reference-icon"]).toBe("clipboard");
  expect(after.props.children[0].props["data-reference-icon"]).toBe("quote");
  expect(after.props["aria-label"]).toBe("引用到 Flops：引用 1 项");
});

it("routes a double failure to a retryable toast without reinserting on retry", async () => {
  vi.stubGlobal("window", { location: { origin: "http://lan-host:8123" }, isSecureContext: true });
  const writeText = vi.fn().mockRejectedValue(new Error("denied"));
  vi.stubGlobal("navigator", { clipboard: { writeText } });
  vi.stubGlobal("document", { createElement: () => { throw new Error("unavailable"); } });
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
  vi.mocked(flopsInsertReferenceAvailable).mockReturnValue(true);
  vi.mocked(flopsInsertReference).mockResolvedValue({ success: false, reason: "busy" });
  await CopyRefButton({ kind: "handle", id: "a" }).props.children[0].props.onClick({ stopPropagation: vi.fn() });
  const toast = vi.mocked(showToast).mock.calls[0][0];
  expect(toast.tone).toBe("error");
  expect(toast.message).toContain("复制失败");
  expect(toast.message).not.toContain("busy");
  expect(toast.action?.label).toBe("重试复制");
  writeText.mockResolvedValue(undefined);
  await toast.action!.run();
  expect(showToast).toHaveBeenLastCalledWith(expect.objectContaining({ message: "已复制引用", tone: "success" }));
  expect(flopsInsertReference).toHaveBeenCalledTimes(1);
});
