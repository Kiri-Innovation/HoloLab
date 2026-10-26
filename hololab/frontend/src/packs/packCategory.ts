type CategorizedPack = {name: string; category?: string[]};

export function collapseRedundantCategory<T extends CategorizedPack>(item: T): T {
  const name = item.name.split('/').pop();
  const category = item.category ?? [];
  return name && category.at(-1) === name ? {...item, category: category.slice(0, -1)} : item;
}
