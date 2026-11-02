// Seeded run log: 6-20 lines, levels in random case, at least one lower-case and one upper-case error or fatal line.
export const entry = "src/report.js";

const LEVELS = ["INFO", "WARN", "ERROR", "FATAL", "DEBUG"];
const MESSAGES = ["boot ok", "db timeout after 30s", "slow query", "out of memory", "retry failed", "cache warmed", "disk almost full", "user login"];

export function fixtures(rng) {
  const count = rng.int(6, 20);
  const levels = Array.from({ length: count }, () => rng.pick(LEVELS));
  levels[0] = "ERROR";
  levels[1] = "FATAL";
  const cases = [
    (l) => l.toLowerCase(),
    (l) => l.toUpperCase(),
    (l) => l[0] + l.slice(1).toLowerCase(),
  ];
  const lines = levels.map((level, i) => {
    const shown = i === 0 ? level.toLowerCase() : i === 1 ? level.toUpperCase() : rng.pick(cases)(level);
    const sec = String(i + 1).padStart(2, "0");
    return `2026-10-30T10:00:${sec}Z ${shown} ${rng.pick(MESSAGES)}`;
  });
  return {
    files: { "data/run.log": `${lines.join("\n")}\n` },
    cases: [{ name: "run log", call: "errorReport", args: ["@data/run.log"] }],
  };
}
