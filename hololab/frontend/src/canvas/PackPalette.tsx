// Left sidebar: hierarchical, searchable palette of available packs.
//
// The tree is grouped by each pack's ``manifest.category`` (a slash-hierarchy
// like ``reconstruction/sharp-4dgs``). Packs without a category fall under
// ``misc/``. A pack is dropped onto the canvas via HTML5 drag-and-drop; the
// canvas handles onDrop, mints a new graph-node id, and inserts it — leaves
// in this tree are the drag sources, same wire contract as before.

import { useEffect, useMemo, useState, type CSSProperties } from "react";
import type { CatalogPack } from "../wire";
import { firstTagColour } from "../tags";
import { CONTROL_STYLE } from "../ui/controlStyles";

export interface PackPaletteProps {
  catalog: CatalogPack[];
  onRefresh: () => void;
}

// ---------------------------------------------------------------------------
// Tree model
// ---------------------------------------------------------------------------

type FolderNode = {
  kind: "folder";
  path: string; // "reconstruction/sharp-4dgs"
  label: string; // "sharp-4dgs"
  depth: number;
  children: TreeNode[];
};

type LeafNode = {
  kind: "leaf";
  path: string; // "reconstruction/sharp-4dgs/track-to-gs-sequence@0.1.0"
  depth: number;
  pack: CatalogPack;
};

type TreeNode = FolderNode | LeafNode;

function buildTree(catalog: CatalogPack[]): FolderNode {
  const root: FolderNode = {
    kind: "folder",
    path: "",
    label: "",
    depth: -1,
    children: [],
  };
  const folderIndex = new Map<string, FolderNode>();
  folderIndex.set("", root);

  const ensureFolder = (segments: string[]): FolderNode => {
    let acc = "";
    let parent = root;
    for (let i = 0; i < segments.length; i++) {
      const seg = segments[i];
      acc = acc ? `${acc}/${seg}` : seg;
      let node = folderIndex.get(acc);
      if (!node) {
        node = {
          kind: "folder",
          path: acc,
          label: seg,
          depth: i,
          children: [],
        };
        folderIndex.set(acc, node);
        parent.children.push(node);
      }
      parent = node;
    }
    return parent;
  };

  for (const pack of catalog) {
    const segs =
      pack.category && pack.category.length > 0 ? pack.category : ["misc"];
    const folder = ensureFolder(segs);
    folder.children.push({
      kind: "leaf",
      path: `${folder.path}/${pack.name}@${pack.version}`,
      depth: folder.depth + 1,
      pack,
    });
  }

  // Stable sort: folders before leaves at each level, then alphabetical.
  const sortRec = (node: FolderNode) => {
    node.children.sort((a, b) => {
      if (a.kind !== b.kind) return a.kind === "folder" ? -1 : 1;
      const la = a.kind === "folder" ? a.label : a.pack.name;
      const lb = b.kind === "folder" ? b.label : b.pack.name;
      return la.localeCompare(lb);
    });
    for (const c of node.children) if (c.kind === "folder") sortRec(c);
  };
  sortRec(root);
  return root;
}

// Match by pack name / description / category segments. Case-insensitive
// substring. Returns the set of leaf paths that match, plus the set of
// ancestor folder paths that must be force-expanded to reveal them.
function computeMatches(root: FolderNode, query: string) {
  const q = query.trim().toLowerCase();
  const matched = new Set<string>();
  const forceOpen = new Set<string>();

  if (!q) return { matched, forceOpen, active: false };

  const visit = (node: TreeNode, ancestors: string[]): boolean => {
    if (node.kind === "leaf") {
      const hay =
        `${node.pack.name} ${node.pack.description ?? ""} ${node.path}`.toLowerCase();
      if (hay.includes(q)) {
        matched.add(node.path);
        for (const a of ancestors) forceOpen.add(a);
        return true;
      }
      return false;
    }
    const nextAncestors = node.path
      ? [...ancestors, node.path]
      : ancestors;
    let anyChild = false;
    for (const child of node.children) {
      if (visit(child, nextAncestors)) anyChild = true;
    }
    return anyChild;
  };

  visit(root, []);
  return { matched, forceOpen, active: true };
}

// ---------------------------------------------------------------------------
// Persistent folder open state
// ---------------------------------------------------------------------------

const LS_KEY = "hololab.packPalette.open";

function loadOpenState(): Record<string, boolean> {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function saveOpenState(state: Record<string, boolean>) {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(state));
  } catch {
    /* ignore quota */
  }
}

// Default: top-level folders open, everything below collapsed. Users can
// override; we only fill in defaults for paths they've never touched.
function withDefaults(
  root: FolderNode,
  saved: Record<string, boolean>,
): Record<string, boolean> {
  const out = { ...saved };
  const walk = (node: FolderNode) => {
    for (const c of node.children) {
      if (c.kind !== "folder") continue;
      if (!(c.path in out)) out[c.path] = c.depth === 0;
      walk(c);
    }
  };
  walk(root);
  return out;
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

const INDENT_PX = 12;

const ROW_BASE: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
  padding: "3px 6px",
  fontSize: "var(--fs-sm)",
  color: "var(--text-body)",
  borderRadius: "var(--radius-sm)",
  userSelect: "none",
};

function IndentGuides({ depth }: { depth: number }) {
  // One thin vertical line per ancestor level; keeps the eye tracking which
  // folder a row belongs to. Very light so it doesn't compete with content.
  if (depth <= 0) return null;
  const guides: JSX.Element[] = [];
  for (let i = 0; i < depth; i++) {
    guides.push(
      <span
        key={i}
        style={{
          display: "inline-block",
          width: INDENT_PX,
          alignSelf: "stretch",
          borderLeft: "1px solid var(--border-subtle)",
          marginLeft: i === 0 ? 2 : 0,
        }}
      />,
    );
  }
  return <>{guides}</>;
}

function Chevron({ open }: { open: boolean }) {
  return (
    <span
      aria-hidden
      style={{
        display: "inline-block",
        width: 12,
        textAlign: "center",
        color: "var(--text-subtle)",
        fontSize: 10,
        lineHeight: 1,
        transform: open ? "rotate(90deg)" : "rotate(0deg)",
        transition: "transform var(--dur-fast) var(--ease)",
      }}
    >
      ▶
    </span>
  );
}

// Wrap the matching substring in a highlight span. Case-insensitive.
function Highlight({ text, query }: { text: string; query: string }) {
  if (!query) return <>{text}</>;
  const idx = text.toLowerCase().indexOf(query.toLowerCase());
  if (idx < 0) return <>{text}</>;
  const before = text.slice(0, idx);
  const match = text.slice(idx, idx + query.length);
  const after = text.slice(idx + query.length);
  return (
    <>
      {before}
      <span
        style={{
          background: "var(--accent-soft)",
          color: "var(--text)",
          borderRadius: 2,
          padding: "0 1px",
        }}
      >
        {match}
      </span>
      {after}
    </>
  );
}

function PackLeaf({
  pack,
  depth,
  query,
}: {
  pack: CatalogPack;
  depth: number;
  query: string;
}) {
  const [hover, setHover] = useState(false);
  return (
    <div
      className="hl-pack-leaf"
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData(
          "application/hololab-pack",
          JSON.stringify({ name: pack.name, version: pack.version }),
        );
        e.dataTransfer.effectAllowed = "copy";
      }}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      title={pack.description ?? `${pack.name}@${pack.version}`}
      style={{
        ...ROW_BASE,
        cursor: "grab",
        background: hover ? "var(--surface-hover)" : "transparent",
      }}
    >
      <IndentGuides depth={depth} />
      {/* Chevron slot kept blank on leaves so folder/leaf labels align. */}
      <span style={{ display: "inline-block", width: 12 }} />
      <span
        style={{
          flex: 1,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          fontWeight: 500,
        }}
      >
        <Highlight text={pack.name} query={query} />
      </span>
      <span
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: "var(--fs-micro)",
          color: "var(--text-subtle)",
        }}
      >
        v{pack.version}
      </span>
      <span style={{ display: "inline-flex", gap: 3, marginLeft: 4 }}>
        {Object.entries(pack.outputs).map(([name, spec]) => (
          <span
            key={`o-${name}`}
            title={`output ${name}: ${spec.tags.join(", ")}`}
            style={{
              width: 6,
              height: 6,
              borderRadius: "var(--radius-pill)",
              background: firstTagColour(spec.tags),
              boxShadow: "0 0 0 1px var(--border)",
            }}
          />
        ))}
      </span>
    </div>
  );
}

function Folder({
  node,
  open,
  onToggle,
  query,
  isMatch,
  renderChildren,
}: {
  node: FolderNode;
  open: boolean;
  onToggle: () => void;
  query: string;
  isMatch: boolean;
  renderChildren: () => JSX.Element[];
}) {
  const [hover, setHover] = useState(false);
  return (
    <>
      <div
        className="hl-pack-folder"
        onClick={onToggle}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        style={{
          ...ROW_BASE,
          cursor: "pointer",
          background: hover ? "var(--surface-hover)" : "transparent",
          color: "var(--text)",
        }}
      >
        <IndentGuides depth={node.depth} />
        <Chevron open={open} />
        <span
          style={{
            flex: 1,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            fontWeight: 600,
            letterSpacing: "0.01em",
          }}
        >
          <Highlight text={node.label} query={isMatch ? query : ""} />
        </span>
      </div>
      {open && renderChildren()}
    </>
  );
}

// ---------------------------------------------------------------------------
// Palette
// ---------------------------------------------------------------------------

export function PackPalette({ catalog, onRefresh }: PackPaletteProps) {
  const [query, setQuery] = useState("");
  const [openState, setOpenState] = useState<Record<string, boolean>>(() =>
    loadOpenState(),
  );

  const tree = useMemo(() => buildTree(catalog), [catalog]);

  // Fill in defaults for any newly-appeared folders without stomping saved
  // user choices. Ran once per tree change; cheap.
  useEffect(() => {
    setOpenState((prev) => {
      const filled = withDefaults(tree, prev);
      // Persist only user-actionable state (which equals filled here since we
      // treat defaults as the initial snapshot the user can then edit).
      saveOpenState(filled);
      return filled;
    });
  }, [tree]);

  const { matched, forceOpen, active } = useMemo(
    () => computeMatches(tree, query),
    [tree, query],
  );

  const toggle = (path: string) => {
    setOpenState((prev) => {
      const next = { ...prev, [path]: !prev[path] };
      saveOpenState(next);
      return next;
    });
  };

  const isFolderMatched = (folder: FolderNode): boolean => {
    // A folder "matches" if any descendant leaf matched — used only for
    // rendering visibility during search.
    if (!active) return true;
    const walk = (n: TreeNode): boolean => {
      if (n.kind === "leaf") return matched.has(n.path);
      return n.children.some(walk);
    };
    return walk(folder);
  };

  const renderChildren = (folder: FolderNode): JSX.Element[] => {
    const out: JSX.Element[] = [];
    for (const child of folder.children) {
      if (child.kind === "leaf") {
        if (active && !matched.has(child.path)) continue;
        out.push(
          <PackLeaf
            key={child.path}
            pack={child.pack}
            depth={child.depth}
            query={active ? query : ""}
          />,
        );
      } else {
        if (!isFolderMatched(child)) continue;
        const open = active
          ? forceOpen.has(child.path) || !!openState[child.path]
          : !!openState[child.path];
        out.push(
          <Folder
            key={child.path}
            node={child}
            open={open}
            onToggle={() => toggle(child.path)}
            query={query}
            isMatch={active && isFolderMatched(child)}
            renderChildren={() => renderChildren(child)}
          />,
        );
      }
    }
    return out;
  };

  const hasCatalog = catalog.length > 0;
  const hasResults = !active || matched.size > 0;

  return (
    <div
      className="hl-dock hl-pack-palette"
      style={{ display: "flex", flexDirection: "column", height: "100%" }}
    >
      <div
        className="hl-dock-heading"
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <h2 className="hl-section-title" style={{ margin: 0 }}>
          Packs
        </h2>
        <button
          onClick={onRefresh}
          style={{ ...CONTROL_STYLE, color: "var(--text-muted)" }}
        >
          Refresh
        </button>
      </div>

      <div
        style={{
          padding: "8px 12px 6px",
          borderBottom: "1px solid var(--border-subtle)",
          background: "var(--surface)",
        }}
      >
        <div style={{ position: "relative" }}>
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search packs…"
            aria-label="Search packs"
            style={{
              width: "100%",
              height: "var(--control-h-md)",
              padding: "0 26px 0 8px",
              boxSizing: "border-box",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius-sm)",
              background: "var(--surface-2)",
              color: "var(--text-body)",
              fontSize: "var(--fs-sm)",
              lineHeight: "var(--lh-ui)",
              outline: "none",
            }}
            onFocus={(e) => {
              e.currentTarget.style.borderColor = "var(--border-focus)";
            }}
            onBlur={(e) => {
              e.currentTarget.style.borderColor = "var(--border)";
            }}
          />
          {query && (
            <button
              onClick={() => setQuery("")}
              aria-label="Clear search"
              style={{
                position: "absolute",
                top: 0,
                right: 0,
                height: "100%",
                width: 24,
                border: "none",
                background: "transparent",
                color: "var(--text-subtle)",
                cursor: "pointer",
                fontSize: 14,
                lineHeight: 1,
              }}
            >
              ×
            </button>
          )}
        </div>
      </div>

      <div style={{ flex: 1, overflow: "auto", padding: "6px 8px 12px" }}>
        {!hasCatalog && (
          <div
            style={{
              fontSize: "var(--fs-xs)",
              color: "var(--text-muted)",
              padding: "12px 6px",
            }}
          >
            No packs offered by any online compute node.
          </div>
        )}
        {hasCatalog && !hasResults && (
          <div
            style={{
              fontSize: "var(--fs-xs)",
              color: "var(--text-muted)",
              padding: "12px 6px",
            }}
          >
            No packs match “{query.trim()}”.
          </div>
        )}
        {hasCatalog && hasResults && renderChildren(tree)}
      </div>
    </div>
  );
}
