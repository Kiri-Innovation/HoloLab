// Only compressed videos are retained; decoded images never enter this cache.
export class PreviewCache {
  constructor(maxEntries = 3, maxBytes = 24 * 1024 * 1024) {
    this.maxEntries = maxEntries; this.maxBytes = maxBytes; this.entries = new Map(); this.bytes = 0;
  }
  key(root, camera) { return JSON.stringify([root, camera]); }
  get(root, camera) {
    const key = camera ? this.key(root, camera) : [...this.entries.keys()].find(k => this.entries.get(k).root === root);
    const entry = this.entries.get(key);
    if (entry) { this.entries.delete(key); this.entries.set(key, entry); }
    return entry;
  }
  put(entry, pinned) {
    const key = this.key(entry.root, entry.camera);
    if (entry.blob.size > this.maxBytes) return false;
    this.remove(key);
    while (this.entries.size >= this.maxEntries || this.bytes + entry.blob.size > this.maxBytes) {
      const victim = [...this.entries.keys()].find(k => k !== pinned);
      if (victim === undefined) return false;
      this.remove(victim);
    }
    this.entries.set(key, entry); this.bytes += entry.blob.size; return true;
  }
  remove(key) { const entry = this.entries.get(key); if (entry) this.bytes -= entry.blob.size; this.entries.delete(key); }
  clear() { this.entries.clear(); this.bytes = 0; }
}
