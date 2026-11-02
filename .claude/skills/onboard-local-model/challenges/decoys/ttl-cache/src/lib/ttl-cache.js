// A map whose entries expire `ttlMs` after they were set; the clock is injected.
export class TtlMap {
  constructor(ttlMs, now = Date.now) {
    this.ttlMs = ttlMs;
    this.now = now;
    this.items = new Map();
  }

  set(key, value) {
    this.items.set(key, { value, expires: this.now() + this.ttlMs });
  }

  get(key) {
    const hit = this.items.get(key);
    if (hit && hit.expires > this.now()) return hit.value;
    this.items.delete(key);
    return undefined;
  }
}
