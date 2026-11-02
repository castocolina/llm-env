// Prompt templates (Decision 15). The first sentence and the last are shared by every generated prompt.
export const P = "Work only inside this directory.";
export const E = "Do not edit the tests or data; reply with one sentence naming the root cause or the files you changed.";

const ticks = (paths) => [...paths].sort((a, b) => (a < b ? -1 : a > b ? 1 : 0)).map((p) => `\`${p}\``).join(", ");

// symptoms = failing test files; siteFile = the (renamed) file that holds the defect; name and spec = the stubbed
// declaration (renamed) and its spec sentence (L2); imported = the src/ files the failing tests import directly (L3);
// feature = the renamed feature statement (L4).
export function buildPrompt({ layer, summary, symptoms, siteFile, name, spec, imported, feature }) {
  if (layer === 1) {
    return `${P} ${summary} Running \`node --test\` shows failing tests in ${ticks(symptoms)}. The cause is in \`${siteFile}\`: find it and fix the source. ${E}`;
  }
  if (layer === 2) {
    return `${P} ${summary} Implement \`${name}\` in \`${siteFile}\` so that \`node --test\` passes. Spec: ${spec} ${E}`;
  }
  if (layer === 3) {
    return `${P} ${summary} \`node --test\` fails in ${ticks(symptoms)}, which exercise ${ticks(imported)}. Several modules under \`src/\` are involved: find the cause and fix the source. ${E}`;
  }
  if (layer === 4) {
    return `${P} ${summary} ${feature} Existing callers must keep working and \`node --test\` must pass. ${E}`;
  }
  if (layer === 5) {
    return `${P} ${summary} The project has many modules; \`node --test\` fails in ${ticks(symptoms)}. Find the root cause rather than patching the reported numbers. ${E}`;
  }
  throw new Error(`no prompt template for layer ${layer}`);
}
