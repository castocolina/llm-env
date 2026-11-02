// A fixed-capacity buffer: pushing past the capacity drops the oldest item.
export class Ring {
  constructor(capacity) {
    this.capacity = capacity;
    this.items = [];
  }

  push(item) {
    this.items.push(item);
    if (this.items.length > this.capacity) this.items.shift();
    return this;
  }

  toArray() {
    return this.items.slice();
  }
}
