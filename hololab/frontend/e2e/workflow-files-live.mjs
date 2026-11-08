// Explicit 4090 acceptance. Restores both real files byte-for-byte in finally.
import { chromium, expect } from "@playwright/test";
import {
  readFile,
  writeFile,
  rename,
  unlink,
  mkdtemp,
  copyFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createHash } from "node:crypto";

const base = "http://127.0.0.1:8828";
const small = "f1a8af3c-56d5-460f-849a-3cf68f06701c";
const classic = "b71fdcaa-ce43-4aa6-9723-a5f9721a9f56";
const api = async (path, body, method = "POST") => {
  const r = await fetch(
    base + path,
    body
      ? {
          method,
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }
      : undefined,
  );
  const data = await r.json();
  if (!r.ok) throw Error(`${path}: ${r.status} ${JSON.stringify(data)}`);
  return data;
};
const atomic = async (path, bytes) => {
  const temp = `${path}.live-${process.pid}`;
  await writeFile(temp, bytes);
  await rename(temp, path);
};
const digest = (b) => createHash("sha256").update(b).digest("hex");
await expect.poll(async () => {
  try { return (await fetch(base + '/health')).status; } catch { return 0; }
}, {timeout:60000,intervals:[500,1000]}).toBe(200);
await expect.poll(async () => (await api('/api/nodes')).length, {timeout:120000,intervals:[1000,2000]}).toBeGreaterThan(0);
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1600, height: 1050 },
});
const first = await context.newPage(),
  second = await context.newPage();
const errors = [];
first.on("pageerror", (e) => errors.push(e.message));
second.on("pageerror", (e) => errors.push(e.message));
const originals = [];
const sockets = [];
const report = {};
const backup = await mkdtemp(join(tmpdir(), "hololab-workflow-live-"));
function receive(ws, predicate) {
  return new Promise((resolve, reject) => {
    const listener = (e) => {
      const frame = JSON.parse(e.data);
      if (frame.kind === "workflow_changed" && predicate(frame.payload)) {
        clearTimeout(timer);
        ws.removeEventListener("message", listener);
        resolve(frame.payload);
      }
    };
    const timer = setTimeout(() => {
      ws.removeEventListener("message", listener);
      reject(Error("WS frame timed out"));
    }, 30000);
    ws.addEventListener("message", listener);
  });
}
async function socket() {
  const ws = new WebSocket(base.replace("http", "ws") + "/ws/frontend");
  await new Promise((resolve, reject) => {
    ws.addEventListener("open", resolve, { once: true });
    ws.addEventListener("error", reject, { once: true });
  });
  sockets.push(ws);
  return ws;
}
try {
  const detail = await api(`/api/workflows/${small}`);
  const bytes = await readFile(detail.path);
  originals.push([detail.path, bytes]);
  await copyFile(detail.path, join(backup, "small.workflow"));
  const source = detail.graph.nodes.find(
    (n) => n.algorithm_name === "k4d/rig-capture-source",
  );
  const old = String(source.params.capture_dir ?? "");
  const edited = old ? old.replace(/\/$/, "") + "/." : "/tmp";
  await Promise.all([
    first.goto(`${base}/w/${small}`, { waitUntil: "domcontentloaded" }),
    second.goto(`${base}/w/${small}`, { waitUntil: "domcontentloaded" }),
  ]);
  const input = first.getByLabel("capture_dir", { exact: true });
  const other = second.getByLabel("capture_dir", { exact: true });
  await expect(input).toBeVisible({ timeout: 60000 });
  await expect(other).toBeVisible({ timeout: 60000 });
  if (old) {
    await expect(input).toHaveValue(old);
    await expect(other).toHaveValue(old);
  }
  const observer = await socket();
  const pushed = receive(
    observer,
    (p) =>
      p.id === small &&
      p.graph?.nodes?.some(
        (n) => n.id === source.id && n.params.capture_dir === edited,
      ),
  );
  await input.fill(edited);
  await pushed;
  await expect(other).toHaveValue(edited, { timeout: 30000 });
  await expect(first.locator('[data-hl-save-status="saved"]')).toBeVisible({
    timeout: 30000,
  });
  report.browser_parameter_edit = { broadcast: true, other_tab_updated: true };
  const sender = await socket();
  const latencies = [];
  let current = await api(`/api/workflows/${small}`);
  for (let i = 0; i < 10; i++) {
    const request_id = `live-${process.pid}-${i}`;
    const a = receive(
        sender,
        (p) => p.id === small && p.request_id === request_id,
      ),
      b = receive(
        observer,
        (p) => p.id === small && p.request_id === request_id,
      );
    const start = performance.now();
    sender.send(
      JSON.stringify({
        kind: "workflow_edit",
        payload: {
          id: small,
          base_rev: current.rev,
          request_id,
          ops: [
            {
              op: "node.pos.set",
              id: source.id,
              position: {
                x: source.position.x + (i % 2 ? 0.25 : 0.125),
                y: source.position.y,
              },
            },
          ],
        },
      }),
    );
    const [ack, remote] = await Promise.all([a, b]);
    if (ack.rejected) throw Error(ack.error);
    latencies.push(performance.now() - start);
    if (remote.rev !== ack.rev) throw Error("rev disagreement");
    current.rev = ack.rev;
  }
  await receive(
    sender,
    (p) => p.id === small && p.rev === current.rev && p.saved,
  );
  latencies.sort((a, b) => a - b);
  report.ws = {
    clients: 2,
    roundtrips: 10,
    min_ms: latencies[0],
    median_ms: latencies[5],
    p95_ms: latencies[9],
  };
  const changed = JSON.parse(await readFile(detail.path, "utf8"));
  changed.name = "7CamArray external acceptance";
  await atomic(detail.path, JSON.stringify(changed, null, 2) + "\n");
  await expect(first.getByPlaceholder("workflow name")).toHaveValue(
    changed.name,
    { timeout: 30000 },
  );
  await expect(second.getByPlaceholder("workflow name")).toHaveValue(
    changed.name,
    { timeout: 30000 },
  );
  report.external_disk_edit = { automatic_reload_both_tabs: true };
  await unlink(detail.path);
  await expect(
    first.getByRole("dialog", { name: "Workflow 文件丢失或身份不匹配" }),
  ).toBeVisible({ timeout: 30000 });
  await expect(
    first.getByRole("button", { name: "重新定位（relink）" }),
  ).toBeVisible();
  await expect(
    first.getByRole("button", { name: "原路径新建空 wf" }),
  ).toBeVisible();
  report.deleted_file = { missing_dialog: true, relink_and_recreate: true };
  await atomic(detail.path, bytes);
  await api("/api/workflows/reload", {});
  await expect(
    first.getByRole("dialog", { name: "Workflow 文件丢失或身份不匹配" }),
  ).toHaveCount(0, { timeout: 30000 });
  // The other real workflow supplies a cheap symlink-only source job. A
  // file-only position marker proves the frozen graph came from disk.
  const real = await api(`/api/workflows/${classic}`);
  const realBytes = await readFile(real.path);
  originals.push([real.path, realBytes]);
  await copyFile(real.path, join(backup, "classic.workflow"));
  const doc = JSON.parse(realBytes.toString());
  const node = doc.graph.nodes.find((n) => n.id === "videos");
  const marker = node.position.x + 0.125;
  node.position.x = marker;
  await atomic(real.path, JSON.stringify(doc, null, 2) + "\n");
  await expect
    .poll(
      async () => {
        const w = await api(`/api/workflows/${classic}`);
        return w.graph.nodes.find((n) => n.id === "videos").position.x;
      },
      { timeout: 30000 },
    )
    .toBe(marker);
  const run = await api(`/api/workflows/${classic}/dispatch/videos`, {});
  report.dispatch = { job_id: run.job_id, snapshot_id: run.snapshot_id };
  await expect
    .poll(
      async () => {
        const job = await api(`/api/jobs/${run.job_id}`);
        report.dispatch.state = job.state;
        return job.state;
      },
      { timeout: 120000, intervals: [500, 1000, 2000] },
    )
    .toBe("done");
  const snapshot = await api(`/api/snapshots/${run.snapshot_id}`);
  if (snapshot.graph.nodes.find((n) => n.id === "videos").position.x !== marker)
    throw Error("dispatch did not freeze file-only marker");
  report.dispatch.file_only_marker = true;
  report.browser_errors = errors;
  if (errors.length) throw Error(JSON.stringify(errors));
} finally {
  for (const ws of sockets) ws.close();
  await context.close();
  await browser.close();
  for (const [path, bytes] of originals) {
    const original = JSON.parse(bytes.toString());
    // Reset the test's cache/bindings edits through the supported CAS API.
    // The file is restored first so missing-file recovery never overwrites
    // another resource; final bytes are the exact pre-test representation.
    await atomic(path, bytes);
    await api("/api/workflows/reload", {});
    const current = await api(`/api/workflows/${original.id}`);
    await api(
      `/api/workflows/${original.id}/content`,
      { ...original, base_rev: current.rev, expected_mtime: current.mtime },
      "PUT",
    );
    await atomic(path, bytes);
    if (digest(await readFile(path)) !== digest(bytes))
      throw Error(`restore failed: ${path}`);
  }
  await api("/api/workflows/reload", {});
  report.original_files_restored = true;
  report.backup = backup;
  console.log(JSON.stringify(report, null, 2));
}
