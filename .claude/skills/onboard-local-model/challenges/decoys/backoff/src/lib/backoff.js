// Exponential retry delay in milliseconds: base * 2^attempt, never above the cap.
export function backoffMs(attempt, baseMs, capMs) {
  return Math.min(capMs, baseMs * 2 ** attempt);
}
