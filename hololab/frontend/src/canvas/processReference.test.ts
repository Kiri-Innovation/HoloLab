import { afterEach, expect, it, vi } from "vitest";
import { copyProcessReference, processReferenceText } from "./processReference";
const p = { pid: 12345, name: "python", used_mb: 1024, nodeId: "node-a", sampledAt: 1 };
afterEach(() => {vi.unstubAllGlobals();});
it("inserts a page link plus process snapshot, not a synthetic entity", async () => {
  const insertReference = vi.fn().mockResolvedValue({success:true});
  vi.stubGlobal("window", {location:{href:"http://localhost/w/a"},flops:{version:1,insertReference}});
  expect((await copyProcessReference(p)).message).toContain("已插入");
  const payload=insertReference.mock.calls[0][0];
  expect(payload.type).toBe("link");
  expect(payload.resource.uri).toBe("http://localhost/w/a");
  expect(payload.snapshot.text).toBe(processReferenceText(p,"http://localhost/w/a"));
});
it.each([undefined, vi.fn().mockResolvedValue({success:false,reason:"unsupported"}),vi.fn().mockRejectedValue(new Error("offline"))])("falls back to clipboard when bridge unavailable or fails", async insertReference => {
  const writeText=vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal("window",{location:{href:"http://localhost/w/a"},isSecureContext:true,flops:insertReference ? {version:1,insertReference}:undefined});
  vi.stubGlobal("navigator",{clipboard:{writeText}});
  expect((await copyProcessReference(p)).message).toContain("已复制");
  expect(writeText).toHaveBeenCalledWith(processReferenceText(p,"http://localhost/w/a"));
  if(insertReference) expect(insertReference).toHaveBeenCalledTimes(1);
});
it('uses metric-specific reference units', () => {
  expect(processReferenceText({...p,metric:'cpu',usage_percent:12.5},'http://localhost')).toContain('CPU: 12.5%');
  expect(processReferenceText({...p,metric:'mem'},'http://localhost')).toContain('MEM: 1.0 GiB');
  expect(processReferenceText({...p,metric:'gpu',usage_percent:null},'http://localhost')).toContain('GPU: Unknown');
});
