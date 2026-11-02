export class LRUCache {
  #m = new Map();
  constructor(n) { this.n = n; }
  get size() { return this.#m.size; }
  get(k) { if (!this.#m.has(k)) return undefined; const v = this.#m.get(k); this.#m.delete(k); this.#m.set(k, v); return v; }
  set(k, v) { this.#m.delete(k); this.#m.set(k, v); if (this.#m.size > this.n) this.#m.delete(this.#m.keys().next().value); }
}
