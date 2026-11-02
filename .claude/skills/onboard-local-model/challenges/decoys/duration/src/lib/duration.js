// 3725000 -> "1h 2m 5s"; zero parts are skipped and 0 stays "0s".
export function formatDuration(ms) {
  let s = Math.floor(ms / 1000);
  const parts = [];
  for (const [size, unit] of [[3600, 'h'], [60, 'm']]) {
    if (s >= size) parts.push(`${Math.floor(s / size)}${unit}`);
    s %= size;
  }
  if (s > 0 || parts.length === 0) parts.push(`${s}s`);
  return parts.join(' ');
}
