// Composable predicates used by ad-hoc reports; each returns a function over normalized events.

export const ofType = (...types) => {
  const wanted = new Set(types);
  return (e) => wanted.has(e.type);
};

export const byUsers = (...users) => {
  const wanted = new Set(users);
  return (e) => wanted.has(e.user);
};

// from <= ts < to, either end may be omitted.
export const between = (from = -Infinity, to = Infinity) => (e) => e.ts >= from && e.ts < to;

export const valueAtLeast = (min) => (e) => e.value >= min;

export const not = (pred) => (e) => !pred(e);

export const allOf = (...preds) => (e) => preds.every((p) => p(e));

export const anyOf = (...preds) => (e) => preds.some((p) => p(e));

export function applyFilters(events, ...preds) {
  const keep = allOf(...preds);
  return events.filter(keep);
}

// Hides user names in shared exports: stable, one-way, 8 hex digits.
export function anonymizeUsers(events, salt = '') {
  const alias = (user) => {
    let h = 2166136261;
    for (const ch of `${salt}${user}`) h = Math.imul(h ^ ch.charCodeAt(0), 16777619) >>> 0;
    return `u${h.toString(16).padStart(8, '0')}`;
  };
  return events.map((e) => ({ ...e, user: alias(e.user) }));
}
