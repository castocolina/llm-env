import { allOf } from '../transform/filters.js';
import { groupBy, sum } from '../util/stats.js';

// A tiny query layer over an EventStore:
//   query(store).where(ofType('view')).between(a, b).countBy('user')
export function query(store) {
  const preds = [];
  const self = {
    where(pred) {
      preds.push(pred);
      return self;
    },
    between(from, to) {
      preds.push((e) => e.ts >= from && e.ts < to);
      return self;
    },
    events() {
      const keep = allOf(...preds);
      return store.all().filter(keep);
    },
    count() {
      return self.events().length;
    },
    sumValues() {
      return sum(self.events().map((e) => e.value));
    },
    countBy(field) {
      return Object.fromEntries([...groupBy(self.events(), (e) => e[field])].map(([k, v]) => [k, v.length]));
    },
    first() {
      return self.events()[0] ?? null;
    },
  };
  return self;
}
