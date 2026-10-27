type CategorizedPack = {name: string; category?: string[]};

export function collapseRedundantCategory<T extends CategorizedPack>(item: T): T {
  const name = item.name.split('/').pop();
  const category = item.category ?? [];
  const last = category.at(-1);
  // A simple trailing category (``merge-colmap`` under ``colmap``) is a
  // meaningful classification, not a repetition.  Only fold a longer,
  // compound category suffix such as ``fastgs-colmap-overlay`` /
  // ``colmap-overlay``.
  return name && last && (last === name || (last.includes('-') && name.endsWith(`-${last}`)))
    ? {...item, category: category.slice(0, -1)} : item;
}
