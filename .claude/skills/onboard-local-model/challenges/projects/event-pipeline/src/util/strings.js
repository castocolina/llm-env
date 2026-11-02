export const trimLower = (s) => String(s ?? '').trim().toLowerCase();

export const isBlank = (s) => String(s ?? '').trim() === '';

// Removes a leading byte-order mark that some exporters write.
export const stripBom = (s) => (s.charCodeAt(0) === 0xfeff ? s.slice(1) : s);

// Splits one CSV line; double quotes may wrap a field and "" is an escaped quote.
export function splitCsvLine(line) {
  const out = [];
  let cur = '';
  let quoted = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (quoted) {
      if (ch === '"' && line[i + 1] === '"') { cur += '"'; i++; }
      else if (ch === '"') quoted = false;
      else cur += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ',') { out.push(cur); cur = ''; }
    else cur += ch;
  }
  out.push(cur);
  return out;
}

export const padRight = (s, n) => String(s).padEnd(n, ' ');
export const padLeft = (s, n) => String(s).padStart(n, ' ');

export function truncate(s, n) {
  const t = String(s);
  return t.length <= n ? t : `${t.slice(0, Math.max(0, n - 1))}…`;
}

// 1234567 -> "1,234,567"
export function withThousands(n) {
  const [int, frac] = String(n).split('.');
  const grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return frac ? `${grouped}.${frac}` : grouped;
}

export const slugify = (s) => trimLower(s).replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');

// Quotes a CSV field when it holds a comma, a quote or a newline.
export function csvField(v) {
  const s = String(v ?? '');
  return /[",\n]/.test(s) ? `"${s.replaceAll('"', '""')}"` : s;
}
