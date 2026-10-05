// SPDX-License-Identifier: Apache-2.0
const assert = require('node:assert/strict');
const { createRequire } = require('node:module');
const dependency = createRequire(`${process.env.DASHBOARD_NODE_MODULES || '/tmp/jarvis-dashboard-tools/node_modules'}/package.json`);
const { chromium } = dependency('playwright');
const base = process.env.DASHBOARD_URL || 'http://127.0.0.1:8769';
let checks = 0;
function check(value, message) { assert.ok(value, message); checks++; }
(async () => {
  const browser = await chromium.launch();
  try {
    for (const width of [1440, 820, 390, 320]) {
      const context = await browser.newContext({ viewport: { width, height: 900 } });
      await context.addInitScript(() => localStorage.setItem('jarvis_token', 'fixture'));
      let online = true, authorized = true, pulled = false;
      const mutations = [];
      await context.route('**/api/**', route => {
        const path = new URL(route.request().url()).pathname;
        if (!authorized) return route.fulfill({ status: 403, json: { detail: 'Forbidden' } });
        let json;
        if (path === '/api/me') json = { username: 'Admin', is_admin: true };
        else if (path.endsWith('/status')) json = { online, version: 'fixture', error: 'Runtime offline', models: online ? [{ name: '<img src=x onerror=alert(1)>', size: 2500000000, details: { parameter_size: '4B' } }] : [], hardware: { cpu_percent: 12, cpu_count: 8, ram_total: 16000000000, ram_available: 8000000000, disk: { free: 10000000000 } } };
        else if (path.endsWith('/catalog')) json = [{ name: 'qwen3:4b', description: 'Qwen 3, 4B', license: 'Apache-2.0', size_estimate: 2500000000, source: 'https://ollama.com/library/qwen3:4b' }];
        else if (path.endsWith('/downloads')) json = pulled ? [{ model: 'qwen3:4b', status: 'DOWNLOADING', detail: 'pulling layer', completed: 47, total: 100 }] : [];
        else if (path.endsWith('/routing') && route.request().method() === 'GET') json = { mode: 'local_first', local_model: 'qwen3:4b' };
        else {
          mutations.push({ path, body: route.request().postDataJSON() });
          if (path.endsWith('/pull')) pulled = true;
          json = path.endsWith('/test') ? { response: 'OK', latency_ms: 42 } : { success: true };
        }
        return route.fulfill({ json });
      });
      const page = await context.newPage(), errors = [];
      page.on('pageerror', error => errors.push(error.message));
      page.on('dialog', dialog => dialog.accept());
      await page.goto(base + '/models');
      await page.locator('#models-content').waitFor({ state: 'visible' });
      await page.locator('#jarvis-shell').waitFor({ state: 'visible' });
      check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `No overflow at ${width}`);
      check(await page.locator('#installed-models img').count() === 0, 'Model name is text, not HTML');
      await page.getByRole('button', { name: 'Modell testen', exact: true }).click();
      await page.waitForFunction(() => document.getElementById('models-message').textContent.includes('42 ms'));
      check(mutations.at(-1).path.endsWith('/test'), 'Test uses backend');
      await page.getByRole('button', { name: 'Installieren', exact: true }).click();
      await page.locator('progress').waitFor();
      check(await page.locator('progress').getAttribute('value') === '47', 'Progress comes from runtime layer counts');
      check(await page.getByRole('button', { name: 'Installieren', exact: true }).isDisabled(), 'Concurrent install prevented in UI');
      await page.reload(); await page.locator('progress').waitFor();
      check(await page.locator('progress').getAttribute('value') === '47', 'Reload restores backend progress');
      await page.screenshot({ path: `/tmp/jarvis-models-${width}.png`, fullPage: true });
      online = false;
      await page.getByRole('button', { name: 'Aktualisieren', exact: true }).click();
      await page.waitForFunction(() => document.getElementById('runtime-status').dataset.online === 'false');
      check(await page.getByRole('button', { name: 'Installieren', exact: true }).isDisabled(), 'Offline cannot install');
      authorized = false;
      await page.getByRole('button', { name: 'Aktualisieren', exact: true }).click();
      await page.locator('#models-content').waitFor({ state: 'hidden' });
      check(await page.locator('#models-login').isVisible(), 'Revoked authorization removes model controls');
      check(!errors.length, `No JS errors: ${errors}`);
      await context.close();
    }
    console.log(`Local models UI: ${checks} assertions passed`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
