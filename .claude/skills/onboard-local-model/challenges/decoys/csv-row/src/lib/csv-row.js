// Joins values into one CSV line, quoting fields that hold a comma, quote or newline.
export function toCsvRow(values) {
  return values
    .map((v) => {
      const s = String(v ?? '');
      return /[",\n]/.test(s) ? `"${s.replaceAll('"', '""')}"` : s;
    })
    .join(',');
}
