'use strict';

const { isAllowedOrigin, resolveBrowserRuntime } = require('./phoenix_browser_runtime.cjs');

(async () => {
  const [base, traceId, marker] = process.argv.slice(2);
  let parsedBase;
  try {
    parsedBase = new URL(base);
  } catch (_) {
    throw new Error('Phoenix browser base URL must be an absolute local HTTP origin');
  }
  if (!['127.0.0.1', 'localhost', '[::1]'].includes(parsedBase.hostname) || parsedBase.protocol !== 'http:') {
    throw new Error('Phoenix browser base URL must use local loopback HTTP');
  }
  const { chromium, executablePath } = resolveBrowserRuntime();
  const browser = await chromium.launch({
    headless: true,
    chromiumSandbox: true,
    executablePath,
    args: ['--disable-gpu'],
  });
  try {
    const context = await browser.newContext();
    await context.route('**/*', (route) => {
      if (isAllowedOrigin(base, route.request().url())) return route.continue();
      return route.abort();
    });
    const page = await context.newPage();
    await page.goto(`${base}/projects`, { waitUntil: 'networkidle', timeout: 30000 });
    const projectLinks = await page.locator('a[href^="/projects/"]').all();
    let href = null;
    for (const link of projectLinks) {
      const label = (await link.innerText()).trim();
      if (label.startsWith('laya')) {
        href = await link.getAttribute('href');
        break;
      }
    }
    if (!href) throw new Error('Phoenix project laya is not listed in the browser');
    const projectId = href.split('/')[2].split('?')[0];
    await page.goto(`${base}/projects/${projectId}/traces/${traceId}`, {
      waitUntil: 'networkidle', timeout: 30000,
    });
    await page.getByText('Input', { exact: true }).waitFor({ timeout: 15000 });
    const body = await page.locator('body').innerText();
    if (!body.includes(marker) || !body.includes('Output')) throw new Error('real trace detail not inspectable in Phoenix UI');
    console.log('Phoenix browser trace detail: PASS');
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(String(error)); process.exit(1); });
