import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { copyReferenceText, sendReferences, type ReferenceInput } from "./referenceAction";
const references: ReferenceInput[] = ["a", "b", "c"].map(id => ({ kind: "handle", id }));
const insert = vi.fn(), writeText = vi.fn(), fetch = vi.fn();
beforeEach(() => {
  vi.stubGlobal("window", { location: { origin: "http://lan-host:8123" }, isSecureContext: true, flops: { version: 1, insertReference: insert } });
  vi.stubGlobal("navigator", { clipboard: { writeText } });
  vi.stubGlobal("fetch", fetch);
  fetch.mockResolvedValue({ ok: true, json: async () => ({ kind: "handle", ref: "hololab://handle/a", resource: {} }) });
  writeText.mockResolvedValue(undefined);
  insert.mockResolvedValue({ success: true });
});
afterEach(() => { vi.unstubAllGlobals(); vi.resetAllMocks(); });
function legacyCopy(result: boolean | Error) {
  const ta = { value: "", style: {}, focus: vi.fn(), select: vi.fn(), remove: vi.fn() };
  const execCommand = vi.fn(() => { if (result instanceof Error) throw result; return result; });
  vi.stubGlobal("document", { createElement: vi.fn(() => ta), body: { appendChild: vi.fn() }, execCommand });
  return { ta, execCommand };
}
it("inserts successfully without touching the clipboard", async () => {
  const result = await sendReferences([references[0]]);
  expect(result.state).toBe("inserted");
  expect(result.message).toContain("已插入 Flops 输入框");
  expect(writeText).not.toHaveBeenCalled();
});
it.each(["false", "throw"])("falls back on insertion %s preserving its reason", async mode => {
  if (mode === "false") insert.mockResolvedValue({ success: false, reason: "forbidden" });
  else insert.mockRejectedValue(new Error("forbidden"));
  const result = await sendReferences([references[0]]);
  expect(result.state).toBe("copied");
  expect(result.message).toContain("插入失败：forbidden");
  expect(result.message).toContain("已复制，可粘贴到对话");
  expect(writeText).toHaveBeenCalledWith("hololab://handle/a");
});
it("reports both failures and permits a copy-only retry", async () => {
  insert.mockResolvedValue({ success: false, reason: "busy" });
  writeText.mockRejectedValue(new Error("denied"));
  const { ta } = legacyCopy(false);
  const result = await sendReferences([references[0]]);
  expect(result.state).toBe("err");
  expect(result.message).toContain("插入失败：busy");
  expect(result.message).toContain("复制失败");
  expect(result.retryText).toBe("hololab://handle/a");
  expect(ta.remove).toHaveBeenCalled();
  writeText.mockResolvedValue(undefined);
  const retry = await copyReferenceText(result.retryText!, result.retryPrefix);
  expect(retry.state).toBe("copied");
  expect(retry.message).toContain("busy");
  expect(insert).toHaveBeenCalledTimes(1);
});
it.each([undefined, { version: 1 }, { version: 2, insertReference: insert }])("copies with absent/old/incompatible API: %j", async flops => {
  Object.assign(window, { flops });
  expect((await sendReferences(references)).state).toBe("copied");
  expect(writeText).toHaveBeenCalledWith("hololab://handle/a\nhololab://handle/b\nhololab://handle/c");
  expect(insert).not.toHaveBeenCalled();
  expect(fetch).not.toHaveBeenCalled();
});
it("uses legacy copying on insecure HTTP without clipboard API", async () => {
  Object.assign(window, { flops: undefined, isSecureContext: false });
  vi.stubGlobal("navigator", {});
  const { ta, execCommand } = legacyCopy(true);
  expect((await sendReferences([references[0]])).state).toBe("copied");
  expect(ta.value).toBe("hololab://handle/a");
  expect(ta.select).toHaveBeenCalled();
  expect(execCommand).toHaveBeenCalledWith("copy");
  expect(ta.remove).toHaveBeenCalled();
});
it("uses legacy copying after modern clipboard rejects", async () => {
  Object.assign(window, { flops: undefined });
  writeText.mockRejectedValue(new Error("denied"));
  const { execCommand } = legacyCopy(true);
  expect((await sendReferences([references[0]])).state).toBe("copied");
  expect(execCommand).toHaveBeenCalledWith("copy");
});
it("reports legacy exceptions and retains retry text", async () => {
  Object.assign(window, { flops: undefined, isSecureContext: false });
  const { ta } = legacyCopy(new Error("unsupported"));
  const result = await sendReferences([references[0]]);
  expect(result.state).toBe("err");
  expect(result.retryText).toBe("hololab://handle/a");
  expect(ta.remove).toHaveBeenCalled();
});
it("inserts batches as separate identities in selection order", async () => {
  const result = await sendReferences(references);
  expect(result.state).toBe("inserted");
  expect(result.message).toContain("3 条");
  expect(insert.mock.calls.map(([p]) => p.resource.uri)).toEqual(["http://lan-host:8123/handle/a", "http://lan-host:8123/handle/b", "http://lan-host:8123/handle/c"]);
  expect(writeText).not.toHaveBeenCalled();
});
it("stops a partial batch and copies only failed/unattempted items", async () => {
  insert.mockResolvedValueOnce({ success: true }).mockResolvedValueOnce({ success: false, reason: "busy" });
  const result = await sendReferences(references);
  expect(result.state).toBe("copied");
  expect(result.message).toContain("已插入 1/3 条");
  expect(result.message).toContain("剩余 2 条");
  expect(insert).toHaveBeenCalledTimes(2);
  expect(writeText).toHaveBeenCalledWith("hololab://handle/b\nhololab://handle/c");
});
it("retains a limited snapshot if resolution fails", async () => {
  fetch.mockRejectedValue(new Error("offline"));
  expect((await sendReferences([references[0]])).state).toBe("inserted");
  expect(insert.mock.calls[0][0].snapshot.text).toContain("unavailable");
});
