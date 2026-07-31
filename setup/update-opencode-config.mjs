import { readFile, rm, writeFile } from "node:fs/promises";

const providerName = "local-llm-env";
const [mode, ...paths] = process.argv.slice(2);

class JsoncParser {
  constructor(text) {
    this.text = text;
    this.index = 0;
  }

  fail() {
    throw new Error("invalid JSONC");
  }

  skipTrivia() {
    while (this.index < this.text.length) {
      if (/[ \t\r\n]/.test(this.text[this.index])) {
        this.index += 1;
      } else if (this.text.startsWith("//", this.index)) {
        const end = this.text.indexOf("\n", this.index + 2);
        this.index = end === -1 ? this.text.length : end + 1;
      } else if (this.text.startsWith("/*", this.index)) {
        const end = this.text.indexOf("*/", this.index + 2);
        if (end === -1) this.fail();
        this.index = end + 2;
      } else {
        return;
      }
    }
  }

  parseDocument() {
    this.skipTrivia();
    const node = this.parseValue();
    this.skipTrivia();
    if (this.index !== this.text.length) this.fail();
    return node;
  }

  parseValue() {
    this.skipTrivia();
    switch (this.text[this.index]) {
      case "{":
        return this.parseObject();
      case "[":
        return this.parseArray();
      case "\"":
        return this.parseString();
      default:
        return this.parsePrimitive();
    }
  }

  parseString() {
    const start = this.index;
    this.index += 1;
    while (this.index < this.text.length) {
      const character = this.text[this.index++];
      if (character === "\"") {
        const raw = this.text.slice(start, this.index);
        return { type: "string", start, end: this.index, value: JSON.parse(raw) };
      }
      if (character.charCodeAt(0) < 0x20) this.fail();
      if (character !== "\\") continue;
      const escape = this.text[this.index++];
      if (!'"\\/bfnrtu'.includes(escape ?? "")) this.fail();
      if (escape !== "u") continue;
      const hex = this.text.slice(this.index, this.index + 4);
      if (!/^[0-9a-fA-F]{4}$/.test(hex)) this.fail();
      this.index += 4;
    }
    this.fail();
  }

  parsePrimitive() {
    const start = this.index;
    while (
      this.index < this.text.length &&
      !/[ \t\r\n,\]}\/]/.test(this.text[this.index])
    ) {
      this.index += 1;
    }
    if (start === this.index) this.fail();
    const raw = this.text.slice(start, this.index);
    let value;
    try {
      value = JSON.parse(raw);
    } catch {
      this.fail();
    }
    if (value !== null && typeof value === "object") this.fail();
    return {
      type: value === null ? "null" : typeof value,
      start,
      end: this.index,
      value,
    };
  }

  parseArray() {
    const start = this.index++;
    const values = [];
    this.skipTrivia();
    if (this.text[this.index] === "]") {
      return { type: "array", start, end: ++this.index, values };
    }
    while (true) {
      values.push(this.parseValue());
      this.skipTrivia();
      if (this.text[this.index] === "]") {
        return { type: "array", start, end: ++this.index, values };
      }
      if (this.text[this.index] !== ",") this.fail();
      this.index += 1;
      this.skipTrivia();
      if (this.text[this.index] === "]") {
        return { type: "array", start, end: ++this.index, values };
      }
    }
  }

  parseObject() {
    const start = this.index++;
    const properties = [];
    this.skipTrivia();
    if (this.text[this.index] === "}") {
      return { type: "object", start, end: ++this.index, properties };
    }
    while (true) {
      this.skipTrivia();
      if (this.text[this.index] !== "\"") this.fail();
      const keyNode = this.parseString();
      this.skipTrivia();
      if (this.text[this.index++] !== ":") this.fail();
      const value = this.parseValue();
      this.skipTrivia();
      let comma;
      if (this.text[this.index] === ",") {
        comma = this.index++;
        this.skipTrivia();
      }
      properties.push({ key: keyNode.value, start: keyNode.start, value, comma });
      if (this.text[this.index] === "}") {
        return { type: "object", start, end: ++this.index, properties };
      }
      if (comma === undefined) this.fail();
    }
  }
}

function readJsonc(text) {
  return new JsoncParser(text).parseDocument();
}

function property(object, key) {
  const matches = object.properties.filter((item) => item.key === key);
  if (matches.length > 1) throw new Error("duplicate configuration key");
  return matches[0];
}

function indentAt(text, offset) {
  const lineStart = text.lastIndexOf("\n", offset - 1) + 1;
  const prefix = text.slice(lineStart, offset);
  return /^[ \t]*$/.test(prefix) ? prefix : "";
}

function renderValue(value, indent) {
  return JSON.stringify(value, null, 2).replaceAll("\n", `\n${indent}`);
}

function appendProperty(text, object, key, value) {
  const last = object.properties.at(-1);
  if (last && last.comma === undefined) {
    text = text.slice(0, last.value.end) + "," + text.slice(last.value.end);
    object = { ...object, end: object.end + 1 };
  }
  const close = object.end - 1;
  const closeLineStart = text.lastIndexOf("\n", close - 1) + 1;
  const insertAt = /^[ \t]*$/.test(text.slice(closeLineStart, close))
    ? closeLineStart
    : close;
  const indent = last
    ? indentAt(text, last.start)
    : `${indentAt(text, object.start)}  `;
  const before = text.slice(0, insertAt);
  const after = text.slice(insertAt);
  const leading = before.endsWith("\n") ? "" : "\n";
  const trailing = after.startsWith("\n") ? "" : "\n";
  return `${before}${leading}${indent}${JSON.stringify(key)}: ${renderValue(value, indent)}${trailing}${after}`;
}

function replaceProvider(text, provider) {
  const root = readJsonc(text);
  if (root.type !== "object") {
    throw new Error("root configuration must be an object");
  }
  const providerProperty = property(root, "provider");
  if (providerProperty === undefined) {
    return appendProperty(text, root, "provider", { [providerName]: provider });
  }
  if (providerProperty.value.type !== "object") {
    throw new Error("provider must be an object");
  }
  const localProvider = property(providerProperty.value, providerName);
  if (localProvider === undefined) {
    return appendProperty(text, providerProperty.value, providerName, provider);
  }
  const indent = indentAt(text, localProvider.start);
  return (
    text.slice(0, localProvider.value.start) +
    renderValue(provider, indent) +
    text.slice(localProvider.value.end)
  );
}

function rejectDuplicateKeys(node) {
  if (node.type === "object") {
    const keys = new Set();
    for (const item of node.properties) {
      if (keys.has(item.key)) throw new Error("duplicate state key");
      keys.add(item.key);
      rejectDuplicateKeys(item.value);
    }
  } else if (node.type === "array") {
    for (const item of node.values) rejectDuplicateKeys(item);
  }
}

function isModelRef(value) {
  return (
    value !== null &&
    !Array.isArray(value) &&
    typeof value === "object" &&
    Object.keys(value).length === 2 &&
    typeof value.providerID === "string" &&
    value.providerID.length > 0 &&
    typeof value.modelID === "string" &&
    value.modelID.length > 0
  );
}

function validateModelState(text) {
  const syntax = readJsonc(text);
  rejectDuplicateKeys(syntax);
  const state = JSON.parse(text);
  if (
    state === null ||
    Array.isArray(state) ||
    typeof state !== "object" ||
    Object.keys(state).sort().join(",") !== "favorite,recent,variant" ||
    !Array.isArray(state.recent) ||
    !state.recent.every(isModelRef) ||
    !Array.isArray(state.favorite) ||
    !state.favorite.every(isModelRef) ||
    state.variant === null ||
    Array.isArray(state.variant) ||
    typeof state.variant !== "object" ||
    !Object.values(state.variant).every((value) => typeof value === "string")
  ) {
    throw new Error("incompatible OpenCode model state");
  }
  return state;
}

function updateModelState(text, models) {
  const state = validateModelState(text);
  if (
    !Array.isArray(models) ||
    models.length === 0 ||
    !models.every(
      (model) =>
        model !== null &&
        typeof model === "object" &&
        typeof model.alias === "string" &&
        model.alias.length > 0,
    ) ||
    new Set(models.map((model) => model.alias)).size !== models.length
  ) {
    throw new Error("invalid enabled model records");
  }
  const local = models.map((model) => ({
    providerID: providerName,
    modelID: model.alias,
  }));
  return JSON.stringify({
    recent: state.recent,
    favorite: local.concat(
      state.favorite.filter((item) => item.providerID !== providerName),
    ),
    variant: state.variant,
  }) + "\n";
}

async function main() {
  if (mode === "--contains-provider" && paths.length === 1) {
    const root = readJsonc(await readFile(paths[0], "utf8"));
    if (root.type !== "object") {
      throw new Error("root configuration must be an object");
    }
    const providerProperty = property(root, "provider");
    if (
      providerProperty !== undefined &&
      providerProperty.value.type !== "object"
    ) {
      throw new Error("provider must be an object");
    }
    const localProvider =
      providerProperty === undefined
        ? undefined
        : property(providerProperty.value, providerName);
    process.exitCode = localProvider === undefined ? 1 : 0;
    return;
  }
  if (mode === "--validate-model-state" && paths.length === 1) {
    validateModelState(await readFile(paths[0], "utf8"));
    return;
  }
  if (mode === "--update-model-state" && paths.length === 3) {
    const [inputPath, modelsPath, outputPath] = paths;
    const models = JSON.parse(await readFile(modelsPath, "utf8"));
    await writeFile(
      outputPath,
      updateModelState(await readFile(inputPath, "utf8"), models),
      { mode: 0o600 },
    );
    return;
  }
  if (mode !== "--replace-provider" || paths.length !== 3) {
    throw new Error("invalid arguments");
  }
  const [inputPath, providerPath, outputPath] = paths;
  const provider = JSON.parse(await readFile(providerPath, "utf8"));
  if (provider === null || Array.isArray(provider) || typeof provider !== "object") {
    throw new Error("provider must be an object");
  }
  await writeFile(
    outputPath,
    replaceProvider(await readFile(inputPath, "utf8"), provider),
    { mode: 0o600 },
  );
}

try {
  await main();
} catch {
  if (
    (mode === "--replace-provider" || mode === "--update-model-state") &&
    paths.length === 3
  ) {
    await rm(paths[2], { force: true });
  }
  process.exitCode = 2;
}
