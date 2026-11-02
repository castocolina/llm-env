// Seeded cases for the paginate project: items 1..n, a page size, pages from the first to one past the end.
export const entry = "src/paginate.js";

export function fixtures(rng) {
  const n = rng.int(5, 40);
  const perPage = rng.int(2, 9);
  const last = Math.ceil(n / perPage);
  const items = Array.from({ length: n }, (_, i) => i + 1);
  const pages = [1, last, last + 1];
  const count = rng.int(6, 10);
  while (pages.length < count) pages.push(rng.int(1, last + 1));
  return {
    cases: pages.map((page, i) => ({ name: `case ${i + 1}: page ${page} of ${n} items by ${perPage}`, call: "paginate", args: [items, page, perPage] })),
  };
}
