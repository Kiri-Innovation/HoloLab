import { useEffect, useRef, useState } from "react";
import { api, type Folder } from "../library/api";

type Placement = { node_id: string; desired_state: string; observed_state: string; relative_path: string; verified_sha256: string | null };
type Resource = { resource_id: string; name: string; size_bytes: number; lifecycle: string; ingest_state: string; sha256: string; placements: Placement[] };
const ready = (r: Resource, node: string) => r.lifecycle === "active" && r.ingest_state === "staged"
  ? r.placements.find(p => p.node_id === node && p.desired_state === "present" && p.observed_state === "ready" && p.verified_sha256 === r.sha256)
  : undefined;

// Library folders are logical containers, not selectable node directories.
export function PathLibraryBrowser({ nodeId, pathKind, onSelect, onCancel }: {
  nodeId: string; pathKind?: "any" | "file" | "directory" | null; onSelect: (path: string) => void; onCancel: () => void;
}) {
  const [folders, setFolders] = useState<Folder[]>([]);
  const [folder, setFolder] = useState("root");
  const [resources, setResources] = useState<Resource[]>([]);
  const [nodeName, setNodeName] = useState(nodeId);
  const [selected, setSelected] = useState<Resource | null>(null);
  const [loading, setLoading] = useState(true);
  const [selecting, setSelecting] = useState(false);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const selection = useRef<AbortController | null>(null);
  useEffect(() => () => selection.current?.abort(), []);
  useEffect(() => {
    const controller = new AbortController();
    const init = { signal: controller.signal };
    setLoading(true); setError(""); setSelected(null); setResources([]);
    void (async () => {
      const allFolders: Folder[] = [];
      for (let offset = 0; ; offset += 1000) {
        const page = await api<{ folders: Folder[] }>(`/library/folders?limit=1000&offset=${offset}`, init);
        allFolders.push(...page.folders);
        if (page.folders.length < 1000) break;
      }
      const allResources: Resource[] = [];
      for (let offset = 0; ; offset += 1000) {
        const page = await api<{ resources: Resource[]; total: number }>(`/library/resources?folder_id=${encodeURIComponent(folder)}&limit=1000&offset=${offset}`, init);
        allResources.push(...page.resources);
        if (offset + 1000 >= page.total) break;
      }
      const { config } = await api<{ config: { node_name: string; library_root?: string } }>(`/nodes/${encodeURIComponent(nodeId)}/config`, init);
      if (!config.library_root?.startsWith("/")) throw new Error("Upgrade this compute node to expose its Library root, then refresh.");
      if (!controller.signal.aborted) {
        setFolders(allFolders); setResources(allResources.filter(r => ready(r, nodeId))); setNodeName(config.node_name || nodeId);
      }
    })().catch(e => { if (!controller.signal.aborted) setError(e.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [nodeId, folder, refresh]);

  async function choose(resource: Resource) {
    if (selecting || pathKind === "directory") return;
    const controller = new AbortController();
    selection.current = controller;
    const init = { signal: controller.signal };
    setSelecting(true); setError("");
    try {
      // Re-read placement and effective root at selection time, then check the file
      // on the assigned node. Never infer an absolute path from a gallery label.
      const fresh = await api<Resource>(`/library/resources/${encodeURIComponent(resource.resource_id)}`, init);
      const placement = ready(fresh, nodeId);
      if (!placement) throw new Error("This resource is no longer ready on this compute node. Refresh the list.");
      const { config } = await api<{ config: { library_root?: string } }>(`/nodes/${encodeURIComponent(nodeId)}/config`, init);
      const root = config.library_root;
      if (!root?.startsWith("/")) throw new Error("Upgrade this compute node to expose its Library root, then refresh.");
      const parts = placement.relative_path.split("/");
      if (parts.some(p => !p || p === "." || p === ".." || p.includes("\\") || p.includes("\0"))) throw new Error("Invalid Library placement path.");
      const filename = parts.pop()!;
      const directory = [root.replace(/\/$/, ""), ...parts].join("/") || "/";
      let offset: number | null = 0;
      let path: string | undefined;
      while (offset !== null) {
        const query: URLSearchParams = new URLSearchParams({ path: directory, prefix: filename, offset: String(offset), limit: "100" });
        const page = await api<{ entries: { name: string; path: string; kind: string; size: number | null }[]; next_offset: number | null }>(`/nodes/${encodeURIComponent(nodeId)}/paths?${query}`, init);
        path = page.entries.find(e => e.name === filename && e.kind === "file" && e.size === fresh.size_bytes)?.path;
        if (path) break;
        offset = page.next_offset;
      }
      if (!path) throw new Error("The Library file is missing or has changed on this compute node. Sync it again before selecting.");
      if (!controller.signal.aborted) onSelect(path);
    } catch (e) { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : String(e)); }
    finally { if (!controller.signal.aborted) setSelecting(false); }
  }
  const crumbs: Folder[] = [];
  const seen = new Set<string>();
  for (let id = folder; id !== "root" && !seen.has(id); ) {
    seen.add(id); const item = folders.find(f => f.folder_id === id); if (!item) break;
    crumbs.unshift(item); id = item.parent_id ?? "root";
  }
  const children = folders.filter(f => f.parent_id === folder);
  return <>
    <p className="hl-path-hint">Only resources synced to {nodeName} are shown. Sync other resources in Library first.</p>
    {pathKind === "directory" && <p className="hl-path-hint">This parameter requires a directory. Use Compute node to select a physical directory; Library folders are for navigation only.</p>}
    <nav aria-label="Library breadcrumbs"><button type="button" disabled={selecting} onClick={() => setFolder("root")}>Library /</button>{crumbs.map(f => <button type="button" disabled={selecting} key={f.folder_id} onClick={() => setFolder(f.folder_id)}>{f.name} /</button>)}</nav>
    {error && <p role="alert">{error}</p>}
    <div className="hl-path-list hl-path-library-list" aria-label="Library entries" aria-busy={loading || selecting}>
      {loading ? <p role="status">Loading…</p> : <>
        {children.map(f => <button type="button" className="hl-path-library-entry" key={f.folder_id} disabled={selecting} onClick={() => setFolder(f.folder_id)}><span>▸ {f.name}</span><small>Folder</small></button>)}
        {resources.map(r => <button type="button" className="hl-path-library-entry" key={r.resource_id} aria-pressed={selected?.resource_id === r.resource_id} disabled={selecting || pathKind === "directory"} onClick={() => setSelected(r)} onDoubleClick={() => void choose(r)}><span>{r.name}</span><small>{r.size_bytes.toLocaleString()} B</small></button>)}
        {!resources.length && <p>No ready files in this folder on {nodeName}. Resources on other devices or still syncing are hidden.</p>}
      </>}
    </div>
    <p className="hl-path-hint">Select a file to use its absolute path on this compute node. The path remains editable.</p>
    <footer><span>{selecting ? "Checking file on compute node…" : selected?.name ?? "Select a file"}</span><button type="button" disabled={loading || selecting} onClick={() => setRefresh(n => n + 1)}>Refresh</button><button type="button" onClick={onCancel}>Cancel</button><button type="button" className="hl-button hl-button--primary" disabled={!selected || loading || selecting || pathKind === "directory"} onClick={() => selected && void choose(selected)}>Select</button></footer>
  </>;
}
