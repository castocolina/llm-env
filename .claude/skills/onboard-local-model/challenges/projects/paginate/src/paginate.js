// Returns the 1-based page `page` of `items`, `perPage` items per page.
export function paginate(items, page, perPage) {
  const start = (page - 1) * perPage;
  return { items: items.slice(start, start + perPage), totalPages: Math.ceil(items.length / perPage) };
}
