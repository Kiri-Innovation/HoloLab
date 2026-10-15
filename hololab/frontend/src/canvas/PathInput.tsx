import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import "./pathInput.css";

type Entry = { name: string; path: string; kind: "directory" | "file" | "other" | "unavailable"; symlink: boolean; size: number | null; modified_ts: number | null };
type Page = { path: string; parent: string; entries: Entry[]; next_offset: number | null };
type Props = { value: string; onChange: (value: string) => void; nodeId: string | null; name: string; pathKind?: "any" | "file" | "directory" | null; initialDirectory?: string };
function split(value: string) {
  const index = value.lastIndexOf("/");
  return index < 0 ? { path: "/", prefix: value } : { path: value.slice(0, index + 1), prefix: value.slice(index + 1) };
}
async function list(node: string, path: string, prefix = "", offset = 0, signal?: AbortSignal): Promise<Page> {
  const query = new URLSearchParams({ path, prefix, offset: String(offset), limit: "100" });
  const response = await fetch(`/api/nodes/${encodeURIComponent(node)}/paths?${query}`, { signal });
  const data = await response.json();
  if (!response.ok) throw new Error(response.status === 504 ? "This node did not reply. Check its connection and upgrade it for path browsing." : typeof data.detail === "string" ? data.detail : `Cannot browse paths (${response.status})`);
  return data;
}
export function PathInput({ value, onChange, nodeId, name, pathKind = "any", initialDirectory = "/" }: Props) {
  const input = useRef<HTMLInputElement>(null);
  const popup = useRef<HTMLDivElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const listbox = useRef<HTMLDivElement>(null);
  const id = useId();
  const [suggest, setSuggest] = useState(false);
  const [suggestions, setSuggestions] = useState<Entry[]>([]);
  const [hint, setHint] = useState("");
  const [active, setActive] = useState(0);
  const [rect, setRect] = useState<{ left: number; top?: number; bottom?: number; width: number; maxHeight: number }>({ left: 0, top: 0, width: 300, maxHeight: 280 });
  const [open, setOpen] = useState(false);
  const [directory, setDirectory] = useState("/");
  const [location, setLocation] = useState("/");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<Page | null>(null);
  const [selected, setSelected] = useState(-1);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const valid = (entry: Entry) => entry.kind !== "unavailable" && entry.kind !== "other" && (!pathKind || pathKind === "any" || entry.kind === pathKind);
  useEffect(() => { setOpen(false); setSuggest(false); }, [nodeId]);
  useEffect(() => {
    if (!suggest || !nodeId) return;
    setSuggestions([]); setHint("Loading…"); setActive(0);
    const controller = new AbortController();
    const timer = setTimeout(() => {
      const { path, prefix } = split(value || initialDirectory + "/");
      list(nodeId, path, prefix, 0, controller.signal).then(data => {
        if (controller.signal.aborted) return;
        setSuggestions(data.entries.filter(e => e.kind === "directory" || valid(e)));
        setHint(data.next_offset !== null ? "More entries available in Browse paths." : data.entries.length ? "" : "No matching entries");
      }).catch(e => { if (!controller.signal.aborted) setHint(e.message); });
    }, 250);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [value, nodeId, suggest, pathKind, initialDirectory]);
  useEffect(() => {
    if (!suggest) return;
    const dismiss = (event: Event) => {
      if (popup.current?.contains(event.target as Node) || input.current === event.target) return;
      setSuggest(false);
    };
    document.addEventListener("pointerdown", dismiss);
    const onScroll = (event: Event) => {
      if (popup.current?.contains(event.target as Node)) return;
      if (document.activeElement === input.current) showSuggestions(); else dismiss(event);
    };
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", dismiss);
    return () => { document.removeEventListener("pointerdown", dismiss); window.removeEventListener("scroll", onScroll, true); window.removeEventListener("resize", dismiss); };
  }, [suggest]);
  useEffect(() => {
    if (open) dialog.current?.showModal(); else dialog.current?.close();
  }, [open]);
  useEffect(() => {
    if (!open || !nodeId) return;
    const controller = new AbortController();
    setBusy(true); setError(""); setSelected(-1); setPage(null);
    list(nodeId, directory, "", offset, controller.signal).then(data => {
      if (!controller.signal.aborted) { setPage(data); setLocation(data.path); }
    }).catch(e => { if (!controller.signal.aborted) setError(e.message); })
      .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [open, nodeId, directory, offset]);
  useEffect(() => { popup.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" }); }, [active]);
  useEffect(() => { listbox.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" }); }, [selected]);
  function showSuggestions() {
    const r = input.current?.getBoundingClientRect();
    if (r) {
      const below = window.innerHeight - r.bottom - 8;
      const maxHeight = Math.min(280, Math.max(below, r.top - 8));
      setRect({ maxHeight, left: Math.min(r.left, Math.max(8, window.innerWidth - 340)), ...(below >= 180 ? { top: r.bottom + 4 } : { bottom: window.innerHeight - r.top + 4 }), width: Math.min(Math.max(r.width, 320), window.innerWidth - 16) });
    }
    setSuggest(true);
  }
  function complete(entry: Entry) {
    onChange(entry.path + (entry.kind === "directory" ? "/" : ""));
    setSuggest(entry.kind === "directory");
    input.current?.focus();
  }
  function navigate(path: string) { setDirectory(path); setLocation(path); setOffset(0); setSelected(-1); }
  function choose(entry: Entry) { if (valid(entry)) { onChange(entry.path); setOpen(false); input.current?.focus(); } }
  const chosen = page?.entries[selected];
  const crumbs = (page?.path ?? directory).split("/").filter(Boolean);
  return <div className="hl-path-input">
    <div className="hl-path-field">
      <input ref={input} aria-label={name} role="combobox" aria-autocomplete="list" aria-expanded={suggest && !!nodeId} aria-controls={suggest ? `${id}-suggestions` : undefined} aria-activedescendant={suggest && suggestions[active] ? `${id}-suggestion-${active}` : undefined} value={value} placeholder="Absolute path on the compute node" onFocus={showSuggestions} onChange={e => { onChange(e.target.value); showSuggestions(); }} onBlur={() => setSuggest(false)} onKeyDown={e => {
        if (e.key === "Escape") { setSuggest(false); e.stopPropagation(); }
        if (suggest && suggestions.length && ["ArrowDown", "ArrowUp", "Tab", "Enter"].includes(e.key)) {
          e.preventDefault(); e.stopPropagation();
          if (e.key === "ArrowDown") setActive(i => (i + 1) % suggestions.length);
          else if (e.key === "ArrowUp") setActive(i => (i + suggestions.length - 1) % suggestions.length);
          else complete(suggestions[active]);
        }
      }} />
      <button type="button" aria-label={`Browse paths for ${name}`} disabled={!nodeId} onClick={() => { setSuggest(false); navigate(value ? split(value).path : initialDirectory); setOpen(true); }}>+</button>
    </div>
    {!nodeId && <small>Select a compute node to browse its files.</small>}
    {suggest && nodeId && createPortal(<div ref={popup} className="hl-path-suggestions" style={rect} onMouseDown={e => e.preventDefault()}>
      <div id={`${id}-suggestions`} role="listbox" aria-label="Path suggestions">{suggestions.map((entry, index) => <div role="option" id={`${id}-suggestion-${index}`} aria-selected={index === active} key={entry.path} onMouseEnter={() => setActive(index)} onClick={() => complete(entry)}>{entry.kind === "directory" ? "▸ " : ""}{entry.name}{entry.kind === "directory" ? "/" : ""}{entry.symlink ? " ↗" : ""}</div>)}</div>
      {hint && <small role="status">{hint}</small>}
    </div>, document.body)}
    {createPortal(<dialog ref={dialog} className="hl-path-dialog" aria-labelledby={`${id}-title`} onCancel={() => setOpen(false)} onClose={() => setOpen(false)}>
      <header><h3 id={`${id}-title`}>Browse compute node · {name}</h3><button type="button" aria-label="Close path browser" onClick={() => setOpen(false)}>×</button></header>
      <form onSubmit={e => { e.preventDefault(); navigate(location); }}><label htmlFor={`${id}-location`}>Location</label><div className="hl-path-field"><input id={`${id}-location`} value={location} onChange={e => setLocation(e.target.value)} /><button type="submit">Go</button></div></form>
      <nav aria-label="Path breadcrumbs"><button type="button" onClick={() => navigate("/")}>/</button>{crumbs.map((part, i) => <button type="button" key={i} onClick={() => navigate("/" + crumbs.slice(0, i + 1).join("/"))}>{part} /</button>)}</nav>
      <p className="hl-path-hint">Files are on the selected compute node. Double-click a folder to open it. ↗ indicates a symlink.</p>
      {error && <p role="alert">{error}</p>}
      <div ref={listbox} className="hl-path-list" role="listbox" aria-label="Directory entries" aria-busy={busy} tabIndex={0} aria-activedescendant={chosen ? `${id}-entry-${selected}` : undefined} onKeyDown={e => {
        if (!page?.entries.length) return;
        if (["ArrowDown", "ArrowUp", "Enter"].includes(e.key)) { e.preventDefault(); e.stopPropagation(); }
        if (e.key === "ArrowDown") setSelected(i => Math.min(i + 1, page.entries.length - 1));
        if (e.key === "ArrowUp") setSelected(i => Math.max(0, i - 1));
        if (e.key === "Enter" && chosen) { if (chosen.kind === "directory") navigate(chosen.path); else choose(chosen); }
      }}>
        {busy && <p role="status">Loading…</p>}
        {!busy && page?.entries.length === 0 && <p>No entries in this page.</p>}
        {page?.entries.map((entry, index) => <div role="option" id={`${id}-entry-${index}`} aria-selected={index === selected} aria-disabled={!valid(entry) && entry.kind !== "directory"} key={entry.path} className="hl-path-entry" onClick={() => { setSelected(index); listbox.current?.focus(); }} onDoubleClick={() => entry.kind === "directory" ? navigate(entry.path) : choose(entry)}><span>{entry.kind === "directory" ? "▸ " : ""}{entry.name}{entry.symlink ? " ↗" : ""}</span><small>{entry.kind}</small><small>{entry.kind === "file" && entry.size !== null ? `${entry.size.toLocaleString()} B` : "—"}</small><small>{entry.modified_ts ? new Date(entry.modified_ts * 1000).toLocaleString() : "—"}</small></div>)}
      </div>
      <div className="hl-path-pages"><button type="button" disabled={!offset || busy} onClick={() => setOffset(0)}>First page</button><button type="button" disabled={page?.next_offset == null || busy} onClick={() => setOffset(page!.next_offset!)}>Next page</button></div>
      <footer><span>{chosen?.name ?? "Select an entry"}</span><button type="button" onClick={() => setOpen(false)}>Cancel</button>{pathKind !== "file" && <button type="button" disabled={!page || busy} onClick={() => { onChange(page!.path); setOpen(false); }}>Use this directory</button>}<button type="button" className="hl-button hl-button--primary" disabled={!chosen || !valid(chosen) || busy} onClick={() => chosen && choose(chosen)}>Select</button></footer>
    </dialog>, document.body)}
  </div>;
}
