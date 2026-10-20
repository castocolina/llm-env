'use strict';

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

function moduleCandidates() {
  const candidates = [];
  try {
    candidates.push(require.resolve('playwright'));
  } catch (_) {
    // Continue through explicit and local cache discovery below.
  }
  const configured = process.env.LLM_ENV_PLAYWRIGHT_ROOT;
  if (configured) candidates.push(path.join(configured, 'index.js'));
  const npmCache = process.env.NPM_CONFIG_CACHE || path.join(os.homedir(), '.npm');
  const npxRoot = path.join(npmCache, '_npx');
  try {
    for (const entry of fs.readdirSync(npxRoot)) {
      candidates.push(path.join(npxRoot, entry, 'node_modules', 'playwright', 'index.js'));
    }
  } catch (_) {
    // A normal package installation does not need an npm npx cache.
  }
  return [...new Set(candidates)];
}

function resolveBrowserRuntime() {
  let playwright;
  let modulePath;
  for (const candidate of moduleCandidates()) {
    try {
      modulePath = require.resolve(candidate);
      playwright = require(modulePath);
      break;
    } catch (_) {
      // Try the next standard or explicitly configured location.
    }
  }
  if (!playwright || !playwright.chromium) {
    throw new Error(
      'Playwright is unavailable. Install it for this project or set LLM_ENV_PLAYWRIGHT_ROOT; ' +
      'set LLM_ENV_CHROMIUM_EXECUTABLE to an existing Chromium binary. No packages are installed automatically.'
    );
  }
  const executablePath = process.env.LLM_ENV_CHROMIUM_EXECUTABLE || playwright.chromium.executablePath();
  if (!executablePath || !fs.existsSync(executablePath) || !fs.statSync(executablePath).isFile()) {
    throw new Error(
      `Chromium executable is unavailable (${executablePath || 'Playwright returned no path'}). ` +
      'Install the browser locally or set LLM_ENV_CHROMIUM_EXECUTABLE to an existing binary.'
    );
  }
  return { chromium: playwright.chromium, modulePath, executablePath };
}

function isAllowedOrigin(base, target) {
  try {
    const baseUrl = new URL(base);
    const targetUrl = new URL(target);
    return ['127.0.0.1', 'localhost', '[::1]'].includes(baseUrl.hostname) &&
      baseUrl.protocol === 'http:' && targetUrl.origin === baseUrl.origin;
  } catch (_) {
    return false;
  }
}

module.exports = { resolveBrowserRuntime, isAllowedOrigin };
