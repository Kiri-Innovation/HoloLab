type CategorizedPack = {name: string; category?: string[]};

export function collapseRedundantCategory<T extends CategorizedPack>(item: T): T {
  const name = item.name.split('/').pop();
  const category = item.category ?? [];
  const last = category.at(-1);
  return name && last && (last === name || name.endsWith(`-${last}`))
    ? {...item, category: category.slice(0, -1)} : item;
}
