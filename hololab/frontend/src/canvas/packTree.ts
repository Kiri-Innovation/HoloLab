import type { CatalogPack } from "../wire";

export type FolderNode = {
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

type ViewLeafNode = { kind: "view"; path: string; depth: number; label: string };
export type TreeNode = FolderNode | LeafNode | ViewLeafNode;

export function buildTree(catalog: CatalogPack[]): FolderNode {
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

  const misc = ensureFolder(["misc"]);
  misc.children.push({ kind: "view", path: "misc/@builtin/visualize", depth: misc.depth + 1, label: "visualize" });

  // Stable sort: folders before leaves at each level, then alphabetical.
  const sortRec = (node: FolderNode) => {
    node.children.sort((a, b) => {
      if ((a.kind === "folder") !== (b.kind === "folder")) return a.kind === "folder" ? -1 : 1;
      const la = a.kind === "leaf" ? a.pack.name : a.label;
      const lb = b.kind === "leaf" ? b.pack.name : b.label;
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
export function computeMatches(root: FolderNode, query: string) {
  const q = query.trim().toLowerCase();
  const matched = new Set<string>();
  const forceOpen = new Set<string>();

  if (!q) return { matched, forceOpen, active: false };

  const visit = (node: TreeNode, ancestors: string[]): boolean => {
    if (node.kind !== "folder") {
      const hay = (node.kind === "view" ? `${node.label} ${node.path}` :
        `${node.pack.name} ${node.pack.description ?? ""} ${node.path}`).toLowerCase();
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
