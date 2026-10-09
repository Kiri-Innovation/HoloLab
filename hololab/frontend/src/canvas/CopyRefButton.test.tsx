import { afterEach, expect, it, vi } from "vitest";
import { CopyRefButton } from "./CopyRefButton";
import { flopsInsertReference, flopsInsertReferenceAvailable } from "../flops";

vi.mock("react", () => ({ useState: () => ["idle", vi.fn()] }));
vi.mock("../flops", () => ({ flopsInsertReference: vi.fn(), flopsInsertReferenceAvailable: vi.fn() }));

afterEach(() => { vi.unstubAllGlobals(); vi.resetAllMocks(); });

it("inserts a workflow using the instance canvas URL, custom display and shared snapshot", async () => {
  vi.stubGlobal("window", { location: { origin: "http://lan-host:8123" }, setTimeout: vi.fn() });
  vi.mocked(flopsInsertReferenceAvailable).mockReturnValue(true);
  vi.mocked(flopsInsertReference).mockResolvedValue({ success: true });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ kind: "workflow", ref: "hololab://workflow/w1", resource: { workflow_id: "w1", name: "Demo", graph: { nodes: [], edges: [] } } }) }));
  const button = CopyRefButton({ kind: "workflow", id: "w1", referenceDisplay: { title: "Demo", subtitle: "0 个节点" } });
  await button.props.onClick({ stopPropagation: vi.fn() });
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
  await button.props.onClick({ stopPropagation: vi.fn() });
  expect(writeText).toHaveBeenCalledWith("hololab://workflow/w1  # Demo · 0 个节点");
  expect(fetch).not.toHaveBeenCalled();
  expect(flopsInsertReference).not.toHaveBeenCalled();
});
