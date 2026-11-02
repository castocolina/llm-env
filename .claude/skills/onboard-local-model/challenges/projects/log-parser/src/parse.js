// "2026-10-30T10:00:01Z ERROR db timeout after 30s" -> {ts, level, msg}
export function parseLine(line) {
  const m = /^(\S+)\s+(\w+)\s+(.*)$/.exec(line);
  return m ? { ts: m[1], level: m[2].toLowerCase(), msg: m[3] } : null;
}
