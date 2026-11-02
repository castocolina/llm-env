export const ERROR_LEVELS = new Set(['error', 'fatal']);
export const isError = (entry) => ERROR_LEVELS.has(entry.level);
