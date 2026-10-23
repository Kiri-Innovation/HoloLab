/** Read-time compatibility only; editing persists native parameters through normal autosave. */
export function arrayParamsForEditor(name: string, version: string, params: Record<string, unknown>): Record<string, unknown> {
  if (name.startsWith("kiri/")) name = name.slice(5);
  if (version !== "0.1.0") return params;
  const next = { ...params };
  try {
    if (name === "video-array-assemble" && !("paths" in next) && typeof next.paths_json === "string") {
      const paths: unknown = JSON.parse(next.paths_json);
      if (!Array.isArray(paths) || !paths.every(v => typeof v === "string")) return params;
      next.paths = paths;
      delete next.paths_json;
    }
    if (["float-array-source", "vec3-array-source"].includes(name) && typeof next.items === "string") {
      const rows: unknown = JSON.parse(next.items);
      const field = name === "float-array-source" ? "offset" : "transform";
      if (!Array.isArray(rows) || !rows.length || rows.some(row => !row || typeof row !== "object" || !(field in row) || Object.keys(row).some(k => k !== field && k !== "id"))) return params;
      if (rows.some(row => "id" in row) && !rows.every(row => "id" in row)) return params;
      next.ids = rows.every(row => "id" in row) ? rows.map(row => row.id) : [];
      if (field === "offset") next.items = rows.map(row => row.offset);
      else {
        if (rows.some(row => !Array.isArray(row.transform) || row.transform.length !== 3)) return params;
        delete next.items;
        ["x", "y", "scale"].forEach((key, i) => { next[key] = rows.map(row => row.transform[i]); });
      }
    }
    return next;
  } catch { return params; }
}
