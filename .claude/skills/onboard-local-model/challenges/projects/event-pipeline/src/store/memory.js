import { assertEvent } from '../util/assert.js';
import { addToIndex, indexSizes } from './index-by.js';

const byUser = (e) => e.user;
const byType = (e) => e.type;

// Append-only in-memory event store with per-user and per-type indexes.
export class EventStore {
  #events = [];
  #users = new Map();
  #types = new Map();

  add(event) {
    assertEvent(event);
    this.#events.push(event);
    addToIndex(this.#users, event, byUser);
    addToIndex(this.#types, event, byType);
    return this;
  }

  addAll(events) {
    for (const e of events) this.add(e);
    return this;
  }

  get size() {
    return this.#events.length;
  }

  all() {
    return this.#events.slice();
  }

  [Symbol.iterator]() {
    return this.#events[Symbol.iterator]();
  }

  forUser(user) {
    return (this.#users.get(user) ?? []).slice();
  }

  forType(type) {
    return (this.#types.get(type) ?? []).slice();
  }

  // Events with from <= ts < to, in insertion order.
  range(from, to) {
    return this.#events.filter((e) => e.ts >= from && e.ts < to);
  }

  bounds() {
    if (this.#events.length === 0) return null;
    const ts = this.#events.map((e) => e.ts);
    return { first: Math.min(...ts), last: Math.max(...ts) };
  }

  userCounts() {
    return indexSizes(this.#users);
  }

  typeCounts() {
    return indexSizes(this.#types);
  }
}

// Distinct users and types seen so far, sorted.
export function distinctUsers(store) {
  return Object.keys(store.userCounts()).sort();
}

export function distinctTypes(store) {
  return Object.keys(store.typeCounts()).sort();
}

// Plain-object digest used in logs: size, time range and the two index sizes.
export function describeStore(store) {
  return { size: store.size, bounds: store.bounds(), users: distinctUsers(store).length, types: distinctTypes(store).length };
}
